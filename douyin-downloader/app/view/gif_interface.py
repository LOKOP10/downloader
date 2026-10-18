"""原生、本地 MP4 转 GIF 页面；工作线程不持有任何 QWidget。

布局沿用转换器页面：左输入队列 / 右转换设置 + 底部结果区。MP4 是视频，
不需要排序合并，只保留批量队列、逐项转换与取消。
"""
import os
from pathlib import Path
import threading

from PySide6.QtCore import QObject, Qt, QUrl, Signal, Slot, QEvent
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QFormLayout,
                               QHBoxLayout, QListWidget, QListWidgetItem,
                               QProgressBar, QSizePolicy, QWidget)
from qfluentwidgets import (CheckBox, ComboBox, FluentIcon as FIF, LineEdit,
                            ListWidget, PrimaryPushButton, PushButton, ScrollArea,
                            TransparentToolButton)

from ..core.ffmpeg import ffmpeg_path
from ..core.mp4_to_gif import ConversionCancelled, ConversionError, convert
from ..core.system_open import open_local_path
from . import design as D
from . import workspace as W


class _Signals(QObject):
    result = Signal(int, str, str, str)
    progress = Signal(int, int, str)
    finished = Signal()


class _ConversionJob:
    """独立线程只保留不可变任务快照、取消标记和信号桥，无 UI 生命周期依赖。"""

    def __init__(self, tasks, options):
        self.signals = _Signals()
        self.cancel = threading.Event()
        self.tasks = tasks
        self.options = options
        self.thread = threading.Thread(target=self.run, name="mp4-gif-converter", daemon=True)

    def run(self):
        try:
            for index, (source, destination) in enumerate(self.tasks):
                if self.cancel.is_set():
                    for pending in range(index, len(self.tasks)):
                        self.signals.result.emit(pending, "已取消", "", "未开始")
                    break
                self.signals.result.emit(index, "处理中", "", "")
                try:
                    info = self._convert(source, destination)
                    detail = (f"{info.get('width', '?')}×{info.get('height', '?')} · "
                              f"{info.get('frames', '?')} 帧") if info else ""
                    self.signals.result.emit(index, "完成", str(destination), detail)
                except ConversionCancelled as error:
                    self.cancel.set()
                    self.signals.result.emit(index, "已取消", "", str(error))
                except Exception as error:
                    # 单文件失败（包括同名输出）不阻断批量后续任务。
                    self.signals.result.emit(index, "失败", "", str(error))
        finally:
            self.signals.finished.emit()

    def _convert(self, source, destination):
        kwargs = dict(self.options, cancel=self.cancel,
                      progress_cb=self.signals.progress.emit)
        return convert(source, destination, **kwargs)


class GifInterface(ScrollArea):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("gifInterface")
        self.setAcceptDrops(True)
        self._busy = False
        self._closed = False
        self._job = None
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.content = QWidget(self)
        self.content.setObjectName("gifContent")
        self.setWidget(self.content)
        D.theme_qss(self.content, "QWidget#gifContent{background:transparent;}",
                    "QWidget#gifContent{background:transparent;}")
        layout = W.page_body(self.content, pad=24, spacing=12)
        layout.addWidget(W.PageHeading(
            "MP4 转 GIF",
            "本地完成，不联网、不上传：把视频片段转成循环 GIF，可批量处理。",
            self.content))

        columns = QHBoxLayout()
        columns.setSpacing(12)

        # ---------------- 左：输入队列 ----------------
        left = W.Panel(self.content, padding=(16, 12, 16, 12), spacing=10)
        left.body.addWidget(W.PanelTitle("输入队列", left, note="逐个转换，不合并"))
        toolbar = QHBoxLayout()
        toolbar.setSpacing(D.GAP_XS)
        self.addBtn = PrimaryPushButton(FIF.ADD, "添加 MP4", left)
        self.addBtn.setToolTip("选择 MP4 文件（支持多选）")
        self.removeBtn = TransparentToolButton(FIF.DELETE, left)
        self.removeBtn.setToolTip("移除选中项（不删除源文件）")
        self.clearBtn = TransparentToolButton(FIF.BROOM, left)
        self.clearBtn.setToolTip("清空列表（不删除源文件）")
        toolbar.addWidget(self.addBtn)
        toolbar.addSpacing(D.GAP_SM)
        for button in (self.removeBtn, self.clearBtn):
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
            "多选或拖入；每个 MP4 独立生成同名 GIF。同名输出默认报错，不会静默覆盖。",
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

        self.fpsCombo = ComboBox(right)
        self.fpsCombo.addItems(["10 帧/秒 · 较小", "15 帧/秒 · 推荐",
                                "20 帧/秒", "24 帧/秒"])
        self.fpsCombo.setCurrentIndex(1)
        self.widthCombo = ComboBox(right)
        self.widthCombo.addItems(["480 px · 推荐", "320 px", "640 px",
                                  "原宽（超过 1920 自动缩小）"])
        self.startEdit = LineEdit(right)
        self.startEdit.setText("0")
        self.startEdit.setPlaceholderText("秒、分:秒 或 时:分:秒，例如 0:05")
        self.durationEdit = LineEdit(right)
        self.durationEdit.setPlaceholderText("留空 = 到结尾（最长 300 秒）")
        self.colorsCombo = ComboBox(right)
        self.colorsCombo.addItems(["256 色 · 推荐", "128 色 · 较小", "64 色 · 最小"])
        self.ditherCombo = ComboBox(right)
        self.ditherCombo.addItems(["Bayer 抖动 · 推荐", "无抖动"])
        self.loopCheck = CheckBox("无限循环（取消则只播放一次）", right)
        self.loopCheck.setChecked(True)

        output = QHBoxLayout()
        output.setSpacing(D.GAP_XS)
        self.outputEdit = LineEdit(right)
        self.outputEdit.setText(str(Path(ctx.config.get("download_path")) / "converted"))
        self.outputBtn = PushButton("选择", right)
        self.outputBtn.setToolTip("选择 GIF 的输出目录")
        output.addWidget(self.outputEdit, 1)
        output.addWidget(self.outputBtn)

        self.overwriteCheck = CheckBox("允许覆盖同名 GIF", right)
        self.overwriteCheck.setChecked(False)
        self.overwriteCheck.setToolTip(
            "默认不覆盖：同名输出会报错。勾选后才允许替换已有文件")

        form.addRow(W.text_label("帧率", right, D.TEXT_SECONDARY, D.FONT_CAPTION),
                    self.fpsCombo)
        form.addRow(W.text_label("宽度", right, D.TEXT_SECONDARY, D.FONT_CAPTION),
                    self.widthCombo)
        form.addRow(W.text_label("起始时间", right, D.TEXT_SECONDARY, D.FONT_CAPTION),
                    self.startEdit)
        form.addRow(W.text_label("转换时长", right, D.TEXT_SECONDARY, D.FONT_CAPTION),
                    self.durationEdit)
        form.addRow(W.text_label("颜色数", right, D.TEXT_SECONDARY, D.FONT_CAPTION),
                    self.colorsCombo)
        form.addRow(W.text_label("抖动", right, D.TEXT_SECONDARY, D.FONT_CAPTION),
                    self.ditherCombo)
        form.addRow(W.text_label("循环", right, D.TEXT_SECONDARY, D.FONT_CAPTION),
                    self.loopCheck)
        form.addRow(W.text_label("输出目录", right, D.TEXT_SECONDARY, D.FONT_CAPTION),
                    output)
        form.addRow(W.text_label("覆盖策略", right, D.TEXT_SECONDARY, D.FONT_CAPTION),
                    self.overwriteCheck)
        right.body.addLayout(form)
        right.body.addStretch(1)
        right.body.addWidget(W.text_label(
            "复用设置里的 ffmpeg 组件；缺失时请先到「设置 → 合并组件」下载。",
            right, D.TEXT_TERTIARY, D.FONT_CAPTION, wrap=True))
        columns.addWidget(right, 1)
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

        self.statusLabel = W.text_label("等待添加 MP4", result, D.TEXT_SECONDARY,
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
        self.removeBtn.clicked.connect(self.remove_selected)
        self.clearBtn.clicked.connect(self.clear_inputs)
        self.outputBtn.clicked.connect(self._choose_output)
        self.startBtn.clicked.connect(self.start_conversion)
        self.cancelBtn.clicked.connect(self.cancel_conversion)
        self.openBtn.clicked.connect(self.open_output_directory)
        self.previewBtn.clicked.connect(self.preview_selected)
        self.resultList.itemSelectionChanged.connect(self._update_preview)
        self.resultList.itemDoubleClicked.connect(self.preview_selected)
        # 实际鼠标常落在列表/输入框子控件，不能只处理外层滚动视口。
        for child in [self.content] + self.content.findChildren(QWidget):
            child.setAcceptDrops(True)
            child.installEventFilter(self)

    # ------------------------------------------------------------- 输入队列
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
            if not path.is_file() or path.suffix.lower() != ".mp4":
                skipped += 1
                continue
            if key not in known:
                item = QListWidgetItem(str(path))
                item.setData(Qt.ItemDataRole.UserRole, str(path))
                self.inputList.addItem(item)
                known.add(key)
        self.statusLabel.setText(f"已添加 {self.inputList.count()} 个 MP4" +
                                 (f"；忽略 {skipped} 个非 MP4 或无效路径" if skipped else ""))

    def _choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "选择 MP4", "", "MP4 视频 (*.mp4 *.MP4)")
        self.add_files(paths)

    def remove_selected(self):
        if not self._busy and not self._closed:
            for item in self.inputList.selectedItems():
                self.inputList.takeItem(self.inputList.row(item))

    def clear_inputs(self):
        if not self._busy and not self._closed:
            self.inputList.clear()

    # ------------------------------------------------------------- 设置
    def _choose_output(self):
        # 原生 Windows 选择器在受限启动环境可能不显示；Qt 自绘对话框不依赖 Shell。
        text = self.outputEdit.text().strip()
        initial = Path(text).expanduser() if text else Path(self.ctx.config.base_dir)
        while not initial.is_dir() and initial.parent != initial:
            initial = initial.parent
        path = QFileDialog.getExistingDirectory(
            self, "选择输出目录", str(initial),
            QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.DontUseNativeDialog)
        if path:
            self.outputEdit.setText(path)
            self.statusLabel.setText("输出目录已设为：" + path)

    def _options(self):
        """读取界面参数；非法输入抛出 ValueError，由调用方转成状态提示。"""
        fps = (10, 15, 20, 24)[self.fpsCombo.currentIndex()]
        width = (480, 320, 640, None)[self.widthCombo.currentIndex()]
        start = _parse_seconds(self.startEdit.text(), default=0.0)
        duration = _parse_seconds(self.durationEdit.text(), default=None)
        colors = (256, 128, 64)[self.colorsCombo.currentIndex()]
        dither = ("bayer", "none")[self.ditherCombo.currentIndex()]
        loop = 0 if self.loopCheck.isChecked() else -1
        return dict(fps=fps, width=width, start=start, duration=duration,
                    colors=colors, dither=dither, loop=loop)

    # ------------------------------------------------------------- 拖放
    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop):
            return self._handle_drag_event(event)
        return super().eventFilter(watched, event)

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
            if any(url.isLocalFile() and url.toLocalFile().lower().endswith(".mp4")
                   for url in event.mimeData().urls()):
                event.acceptProposedAction()

    def dropEvent(self, event):
        if not self._busy and not self._closed:
            self.add_files([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
            event.acceptProposedAction()

    # ------------------------------------------------------------- 转换
    def start_conversion(self):
        if self._busy or self._closed:
            return
        sources = self.input_paths()
        if not sources:
            self.statusLabel.setText("请先添加 MP4 文件")
            return
        ffmpeg = ffmpeg_path(self.ctx.config.base_dir)
        if not ffmpeg:
            self.statusLabel.setText("未找到 FFmpeg，请前往设置下载 FFmpeg 组件，然后重试。不会自动联网。")
            return
        output_text = self.outputEdit.text().strip()
        if not output_text:
            self.statusLabel.setText("请选择输出目录")
            return
        try:
            settings = self._options()
        except ValueError as error:
            self.statusLabel.setText(str(error))
            return
        output = Path(output_text).expanduser().resolve()
        tasks = [(source, output / (Path(source).stem + ".gif")) for source in sources]
        self.resultList.clear()
        self.resultList.show()
        for inputs, destination in tasks:
            item = QListWidgetItem(f"等待 · {destination.name}")
            item.setToolTip(inputs)
            self.resultList.addItem(item)
        options = dict(settings, overwrite=self.overwriteCheck.isChecked(), ffmpeg=ffmpeg)
        job = _ConversionJob(tasks, options)
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
        if item is None:
            return
        destination = self._job.tasks[index][1]
        item.setText(f"{status} · {destination.name}" + (f" · {detail}" if detail else ""))
        item.setData(Qt.ItemDataRole.UserRole, path or None)
        item.setToolTip(str(destination) + (("\n" + detail) if detail else ""))
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

    # ------------------------------------------------------------- 结果
    def _update_preview(self):
        item = self.resultList.currentItem()
        self.previewBtn.setEnabled(bool(item and item.data(Qt.ItemDataRole.UserRole)))

    def open_output_directory(self):
        text = self.outputEdit.text().strip()
        if not text:
            self.statusLabel.setText("请先选择输出目录")
            return
        try:
            path = Path(text).expanduser().resolve()
            if not path.is_dir():
                self.statusLabel.setText("输出目录尚不存在，请先选择已有目录或完成一次转换：" + str(path))
                return
            opened = open_local_path(path, lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))))
            self.statusLabel.setText(("已请求打开：" if opened else "系统未能打开目录，请复制路径到资源管理器：") + str(path))
        except (OSError, ValueError) as error:
            self.statusLabel.setText("打开目录失败：" + str(error))

    def preview_selected(self, *_):
        item = self.resultList.currentItem()
        path = item.data(Qt.ItemDataRole.UserRole) if item else None
        if path and Path(path).is_file():
            try:
                opened = open_local_path(path, lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(path)))
                if not opened:
                    self.statusLabel.setText("系统未能打开 GIF，请复制路径到浏览器或图片查看器：" + path)
            except OSError as error:
                self.statusLabel.setText("打开 GIF 失败：" + str(error))
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


def _parse_seconds(text, default):
    """把「12」「12.5」「1:23」「01:02:03」解析成秒；空文本返回 default。"""
    text = (text or "").strip()
    if not text:
        return default
    parts = text.split(":")
    if len(parts) > 3:
        raise ValueError("时间格式应为秒数、分:秒 或 时:分:秒，例如 12、12.5、1:23、01:02:03")
    try:
        numbers = [float(part) for part in parts]
    except ValueError as exc:
        raise ValueError("时间格式应为秒数、分:秒 或 时:分:秒，例如 12、12.5、1:23、01:02:03") from exc
    if any(number < 0 for number in numbers):
        raise ValueError("时间不能为负数")
    value = 0.0
    for number in numbers:
        value = value * 60 + number
    return value
