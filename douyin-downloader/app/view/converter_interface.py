"""原生、本地 WebP 转换页面；工作线程不持有任何 QWidget。

布局：左右两栏（输入队列 / 转换设置）+ 底部结果区 —— 参数与结果同屏可见，
不用在一条长列表里上下翻。
"""
import os
from pathlib import Path
import threading

from PySide6.QtCore import QObject, Qt, QUrl, Signal, Slot, QEvent
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QColorDialog, QFileDialog,
                               QFormLayout, QHBoxLayout, QListWidget,
                               QListWidgetItem, QProgressBar, QSizePolicy,
                               QWidget)
from qfluentwidgets import (CheckBox, ComboBox, FluentIcon as FIF, LineEdit,
                            ListWidget, PrimaryPushButton, PushButton, ScrollArea,
                            TransparentToolButton)

from ..core.ffmpeg import ffmpeg_path
from ..core.webp_converter import ConversionCancelled, ConversionError, convert, merge_webps
from . import design as D
from . import workspace as W


class _Signals(QObject):
    result = Signal(int, str, str, str)
    progress = Signal(int, int, str)
    finished = Signal()


class _ConversionJob:
    """独立线程只保留不可变任务快照、取消标记和信号桥，无 UI 生命周期依赖。"""
    def __init__(self, tasks, merge, options):
        self.signals = _Signals()
        self.cancel = threading.Event()
        self.tasks = tasks
        self.merge = merge
        self.options = options
        self.thread = threading.Thread(target=self.run, name="webp-converter", daemon=True)

    def run(self):
        try:
            for index, (sources, destination) in enumerate(self.tasks):
                if self.cancel.is_set():
                    for pending in range(index, len(self.tasks)):
                        self.signals.result.emit(pending, "已取消", "", "未开始")
                    break
                self.signals.result.emit(index, "处理中", "", "")
                try:
                    kwargs = dict(self.options, cancel=self.cancel,
                                  progress_cb=self.signals.progress.emit)
                    if self.merge:
                        merge_webps(sources, destination, **kwargs)
                    else:
                        convert(sources[0], destination, **kwargs)
                    self.signals.result.emit(index, "完成", str(destination), "")
                except ConversionCancelled as error:
                    self.cancel.set()
                    self.signals.result.emit(index, "已取消", "", str(error))
                except Exception as error:
                    # 单文件失败（包括同名输出）不阻断批量后续任务。
                    self.signals.result.emit(index, "失败", "", str(error))
        finally:
            self.signals.finished.emit()


class ConverterInterface(ScrollArea):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("converterInterface")
        self.setAcceptDrops(True)
        self._busy = False
        self._closed = False
        self._job = None
        self._background = "#ffffff"
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.content = QWidget(self)
        self.content.setObjectName("converterContent")
        self.setWidget(self.content)
        D.theme_qss(self.content, "QWidget#converterContent{background:transparent;}",
                    "QWidget#converterContent{background:transparent;}")
        layout = W.page_body(self.content, pad=24, spacing=12)
        layout.addWidget(W.PageHeading(
            "WebP 转 MP4",
            "本地完成，不联网、不上传：动图逐帧转 MP4，或按列表顺序合并成一个视频。",
            self.content))

        columns = QHBoxLayout()
        columns.setSpacing(12)

        # ---------------- 左：输入队列 ----------------
        left = W.Panel(self.content, padding=(16, 12, 16, 12), spacing=10)
        leftHead = W.PanelTitle("输入队列", left, note="按列表顺序合并")
        left.body.addWidget(leftHead)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(D.GAP_XS)
        self.addBtn = PrimaryPushButton(FIF.ADD, "添加 WebP", left)
        self.addBtn.setToolTip("选择 WebP 文件（支持多选）")
        # 列表内操作用图标按钮：它们只作用于选中项，用文字会跟「添加」抢主次。
        # 图标按钮必须显式给边长，否则放进 QHBoxLayout 会被拉成大方块。
        self.upBtn = TransparentToolButton(FIF.UP, left)
        self.upBtn.setToolTip("上移选中项（决定合并顺序）")
        self.downBtn = TransparentToolButton(FIF.DOWN, left)
        self.downBtn.setToolTip("下移选中项")
        self.removeBtn = TransparentToolButton(FIF.DELETE, left)
        self.removeBtn.setToolTip("移除选中项（不删除源文件）")
        self.clearBtn = TransparentToolButton(FIF.BROOM, left)
        self.clearBtn.setToolTip("清空列表（不删除源文件）")
        toolbar.addWidget(self.addBtn)
        toolbar.addSpacing(D.GAP_SM)
        for button in (self.upBtn, self.downBtn, self.removeBtn, self.clearBtn):
            button.setFixedSize(32, 32)
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        left.body.addLayout(toolbar)

        self.inputList = ListWidget(left)
        self.inputList.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.inputList.setMinimumHeight(180)
        self.inputList.setAcceptDrops(False)
        left.body.addWidget(self.inputList, 1)
        left.body.addWidget(W.text_label(
            "多选或拖入；合并播放顺序以上方列表为准。同名输出默认报错，不会静默覆盖。",
            left, D.TEXT_TERTIARY, D.FONT_CAPTION, wrap=True))
        columns.addWidget(left, 1)

        # ---------------- 右：转换设置 ----------------
        right = W.Panel(self.content, padding=(16, 12, 16, 12), spacing=10)
        self.editPanel = right
        right.body.addWidget(W.PanelTitle("转换设置", right))

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(D.GAP_MD)
        form.setVerticalSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.modeCombo = ComboBox(right)
        self.modeCombo.addItems(["逐个转换（单个 / 批量）", "按列表顺序合并为一个 MP4"])
        self.crfCombo = ComboBox(right)
        self.crfCombo.addItems(["CRF 12 · 高保真（有损）", "CRF 18 · 推荐",
                                "CRF 23 · 较小文件"])
        self.crfCombo.setCurrentIndex(1)
        self.backgroundBtn = PushButton("背景色 #ffffff", right)
        self.backgroundBtn.setToolTip("WebP 的透明区域会被合成到这个不透明背景色上")

        output = QHBoxLayout()
        output.setSpacing(D.GAP_XS)
        self.outputEdit = LineEdit(right)
        self.outputEdit.setText(str(Path(ctx.config.get("download_path")) / "converted"))
        self.outputBtn = PushButton("选择", right)
        self.outputBtn.setToolTip("选择转换结果的输出目录")
        output.addWidget(self.outputEdit, 1)
        output.addWidget(self.outputBtn)

        self.mergeNameEdit = LineEdit(right)
        self.mergeNameEdit.setText("merged.mp4")
        self.mergeNameEdit.setEnabled(False)
        self.mergeNameEdit.setToolTip("合并模式下的输出文件名（必须是 .mp4）")
        self.overwriteCheck = CheckBox("允许覆盖同名 MP4", right)
        self.overwriteCheck.setChecked(False)
        self.overwriteCheck.setToolTip(
            "默认不覆盖：同名输出会报错。勾选后才允许替换已有文件")

        form.addRow(W.text_label("转换方式", right, D.TEXT_SECONDARY, D.FONT_CAPTION),
                    self.modeCombo)
        form.addRow(W.text_label("画质", right, D.TEXT_SECONDARY, D.FONT_CAPTION),
                    self.crfCombo)
        form.addRow(W.text_label("透明区背景", right, D.TEXT_SECONDARY, D.FONT_CAPTION),
                    self.backgroundBtn)
        form.addRow(W.text_label("输出目录", right, D.TEXT_SECONDARY, D.FONT_CAPTION),
                    output)
        form.addRow(W.text_label("合并文件名", right, D.TEXT_SECONDARY, D.FONT_CAPTION),
                    self.mergeNameEdit)
        form.addRow(W.text_label("覆盖策略", right, D.TEXT_SECONDARY, D.FONT_CAPTION),
                    self.overwriteCheck)
        right.body.addLayout(form)
        right.body.addStretch(1)
        right.body.addWidget(W.text_label(
            "复用设置里的 ffmpeg 组件；缺失时请先到「设置 → 合并组件」下载。",
            right, D.TEXT_TERTIARY, D.FONT_CAPTION, wrap=True))
        columns.addWidget(right, 1)
        # 左右两栏吃掉剩余高度（两栏里的列表都是 stretch=1），
        # 结果区按内容高度贴在下面
        layout.addLayout(columns, 10)

        # ---------------- 结果区 ----------------
        result = W.Panel(self.content, padding=(16, 12, 16, 12), spacing=8)
        result.setSizePolicy(QSizePolicy.Policy.Preferred,
                             QSizePolicy.Policy.Maximum)
        actions = QHBoxLayout()
        actions.setSpacing(D.GAP_SM)
        self.startBtn = PrimaryPushButton(FIF.PLAY, "开始转换", result)
        self.cancelBtn = PushButton(FIF.CLOSE, "取消", result)
        self.cancelBtn.setEnabled(False)
        self.openBtn = PushButton(FIF.FOLDER, "打开输出目录", result)
        self.previewBtn = PushButton(FIF.VIEW, "预览结果", result)
        self.previewBtn.setEnabled(False)
        for button in (self.startBtn, self.cancelBtn, self.openBtn,
                       self.previewBtn):
            actions.addWidget(button)
        actions.addStretch(1)
        result.body.addLayout(actions)

        self.statusLabel = W.text_label("等待添加 WebP", result, D.TEXT_SECONDARY,
                                        D.FONT_CAPTION, wrap=True)
        result.body.addWidget(self.statusLabel)
        self.progressBar = QProgressBar(result)
        self.progressBar.setTextVisible(False)
        self.progressBar.setFixedHeight(4)
        D.theme_qss(self.progressBar,
                    "QProgressBar{border:0;border-radius:2px;background:#E4E4E7;}"
                    "QProgressBar::chunk{background:#009faa;border-radius:2px;}",
                    "QProgressBar{border:0;border-radius:2px;background:#27272A;}"
                    "QProgressBar::chunk{background:#009faa;border-radius:2px;}")
        self.progressBar.setRange(0, 1)
        self.progressBar.setValue(0)
        result.body.addWidget(self.progressBar)

        # 结果列表：没有结果时整块收起（空列表占着 160px 只会让页面像没做完）
        self.resultList = ListWidget(result)
        self.resultList.setFixedHeight(140)
        self.resultList.setSizePolicy(QSizePolicy.Policy.Preferred,
                                      QSizePolicy.Policy.Fixed)
        self.resultList.hide()
        result.body.addWidget(self.resultList)
        layout.addWidget(result, 0)
        layout.addStretch(1)
        self.statusText = self.statusLabel

        self.addBtn.clicked.connect(self._choose_files)
        self.upBtn.clicked.connect(lambda: self.move_selected(-1))
        self.downBtn.clicked.connect(lambda: self.move_selected(1))
        self.removeBtn.clicked.connect(self.remove_selected)
        self.clearBtn.clicked.connect(self.clear_inputs)
        self.modeCombo.currentIndexChanged.connect(self._mode_changed)
        self.backgroundBtn.clicked.connect(self._choose_background)
        self.outputBtn.clicked.connect(self._choose_output)
        self.startBtn.clicked.connect(self.start_conversion)
        self.cancelBtn.clicked.connect(self.cancel_conversion)
        self.openBtn.clicked.connect(self.open_output_directory)
        self.previewBtn.clicked.connect(self.preview_selected)
        self.resultList.itemSelectionChanged.connect(self._update_preview)
        self.resultList.itemDoubleClicked.connect(self.preview_selected)

    def input_paths(self):
        return [self.inputList.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.inputList.count())]

    def add_files(self, paths):
        if self._busy or self._closed:
            return
        known = {os.path.normcase(p) for p in self.input_paths()}
        skipped = 0
        for path in paths:
            path = Path(path).expanduser().resolve()
            key = os.path.normcase(str(path))
            if not path.is_file() or path.suffix.lower() != ".webp":
                skipped += 1
                continue
            if key not in known:
                item = QListWidgetItem(str(path))
                item.setData(Qt.ItemDataRole.UserRole, str(path))
                self.inputList.addItem(item)
                known.add(key)
        self.statusLabel.setText(f"已添加 {self.inputList.count()} 个 WebP" +
                                 (f"；忽略 {skipped} 个非 WebP 或无效路径" if skipped else ""))

    def _choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "选择 WebP", "", "WebP (*.webp *.WEBP)")
        self.add_files(paths)

    def move_selected(self, direction):
        if self._busy or self._closed:
            return
        rows = sorted(self.inputList.row(item) for item in self.inputList.selectedItems())
        selected = set(rows)
        for row in (rows if direction < 0 else reversed(rows)):
            target = row + direction
            if 0 <= target < self.inputList.count() and target not in selected:
                item = self.inputList.takeItem(row)
                self.inputList.insertItem(target, item)
                item.setSelected(True)
                selected.remove(row)
                selected.add(target)

    def remove_selected(self):
        if not self._busy and not self._closed:
            for item in self.inputList.selectedItems():
                self.inputList.takeItem(self.inputList.row(item))

    def clear_inputs(self):
        if not self._busy and not self._closed:
            self.inputList.clear()

    def _mode_changed(self, index):
        self.mergeNameEdit.setEnabled(index == 1)

    def _choose_background(self):
        color = QColorDialog.getColor(QColor(self._background), self, "选择不透明背景色")
        if color.isValid():
            self._background = color.name(QColor.NameFormat.HexRgb)
            self.backgroundBtn.setText(f"背景色：{self._background}")

    def _choose_output(self):
        path = QFileDialog.getExistingDirectory(self, "选择输出目录", self.outputEdit.text())
        if path:
            self.outputEdit.setText(path)

    def _handle_drag_event(self, event):
        if event.type() in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            self.dragEnterEvent(event)
            return True
        if event.type() == QEvent.Type.Drop:
            self.dropEvent(event)
            return True
        return False

    def event(self, event):
        if event.type() in (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop):
            return self._handle_drag_event(event)
        return super().event(event)

    def viewportEvent(self, event):
        if event.type() in (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop):
            return self._handle_drag_event(event)
        return super().viewportEvent(event)

    def dragEnterEvent(self, event):
        if not self._busy and not self._closed and event.mimeData().hasUrls():
            if any(url.isLocalFile() and url.toLocalFile().lower().endswith(".webp")
                   for url in event.mimeData().urls()):
                event.acceptProposedAction()

    def dropEvent(self, event):
        if not self._busy and not self._closed:
            self.add_files([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
            event.acceptProposedAction()

    def start_conversion(self):
        if self._busy or self._closed:
            return
        sources = self.input_paths()
        if not sources:
            self.statusLabel.setText("请先添加 WebP 文件")
            return
        ffmpeg = ffmpeg_path(self.ctx.config.base_dir)
        if not ffmpeg:
            self.statusLabel.setText("未找到 FFmpeg，请前往设置下载 FFmpeg 组件，然后重试。不会自动联网。")
            return
        output_text = self.outputEdit.text().strip()
        if not output_text:
            self.statusLabel.setText("请选择输出目录")
            return
        output = Path(output_text).expanduser().resolve()
        merge = self.modeCombo.currentIndex() == 1
        name = self.mergeNameEdit.text().strip()
        if merge and (not name or name in (".", "..") or any(c in name for c in '/\\<>:"|?*')
                      or Path(name).suffix.lower() != ".mp4"):
            self.statusLabel.setText("合并文件名必须是 MP4 文件名，不能包含目录或非法字符")
            return
        tasks = ([(sources, output / name)] if merge else
                 [([source], output / (Path(source).stem + ".mp4")) for source in sources])
        self.resultList.clear()
        self.resultList.show()
        for inputs, destination in tasks:
            item = QListWidgetItem(f"等待 · {destination.name}")
            item.setToolTip("\n".join(inputs))
            self.resultList.addItem(item)
        options = dict(background=self._background, crf=(12, 18, 23)[self.crfCombo.currentIndex()],
                       overwrite=self.overwriteCheck.isChecked(), ffmpeg=ffmpeg)
        job = _ConversionJob(tasks, merge, options)
        self._job = job
        self._busy = True
        self.editPanel.setEnabled(False)
        self.startBtn.setEnabled(False)
        self.cancelBtn.setEnabled(True)
        self.statusLabel.setText("正在转换…")
        self.progressBar.setRange(0, 0)
        job.signals.result.connect(self._on_result, Qt.ConnectionType.QueuedConnection)
        job.signals.progress.connect(self._on_progress, Qt.ConnectionType.QueuedConnection)
        job.signals.finished.connect(self._on_finished, Qt.ConnectionType.QueuedConnection)
        # 即使主窗口未显式 shutdown，QObject 销毁也会通知引擎取消。
        self.destroyed.connect(job.cancel.set)
        job.thread.start()

    @Slot(int, str, str, str)
    def _on_result(self, index, status, path, detail):
        if self._closed:
            return
        item = self.resultList.item(index)
        destination = self._job.tasks[index][1]
        item.setText(f"{status} · {destination.name}" + (f" · {detail}" if detail else ""))
        item.setData(Qt.ItemDataRole.UserRole, path or None)
        item.setToolTip(str(destination) + (f"\n{detail}" if detail else ""))
        self._update_preview()

    @Slot(int, int, str)
    def _on_progress(self, done, total, message):
        if self._closed:
            return
        self.statusLabel.setText(message)
        self.progressBar.setRange(0, max(0, total))
        self.progressBar.setValue(max(0, min(done, total)))

    @Slot()
    def _on_finished(self):
        self._busy = False
        if self._closed:
            return
        self.editPanel.setEnabled(True)
        self.startBtn.setEnabled(True)
        self.cancelBtn.setEnabled(False)
        self.progressBar.setRange(0, 1)
        self.progressBar.setValue(1)
        self.statusLabel.setText("已取消，已完成的结果仍可预览" if self._job.cancel.is_set() else "处理结束，请查看各项结果")
        self.destroyed.disconnect(self._job.cancel.set)

    def cancel_conversion(self):
        if self._busy and self._job:
            self._job.cancel.set()
            self.cancelBtn.setEnabled(False)
            self.statusLabel.setText("正在取消，等待当前任务清理…")

    def _update_preview(self):
        item = self.resultList.currentItem()
        self.previewBtn.setEnabled(bool(item and item.data(Qt.ItemDataRole.UserRole)))

    def open_output_directory(self):
        path = Path(self.outputEdit.text()).expanduser().resolve()
        if not path.is_dir():
            self.statusLabel.setText("输出目录尚不存在，转换成功后再打开")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def preview_selected(self, *_):
        item = self.resultList.currentItem()
        path = item.data(Qt.ItemDataRole.UserRole) if item else None
        if path and Path(path).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        elif path:
            self.statusLabel.setText("结果文件不存在或已被移动")

    def shutdown(self):
        """主窗口 closeEvent 可直接调用；绝不在 GUI 线程 join / wait。"""
        self._closed = True
        if self._job:
            self._job.cancel.set()
        self.startBtn.setEnabled(False)
        self.cancelBtn.setEnabled(False)
        self.editPanel.setEnabled(False)

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)
