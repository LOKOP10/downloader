# -*- coding: utf-8 -*-
"""下载任务页：进度、状态、操作与运行日志

界面重做的结构：

    页头（任务统计 + 全局操作）
    ┌ 队列面板 ─────────────────────────────────────────────┐
    │ [全部 n] [进行中 n] [已完成 n] [失败 n]   ← 分段筛选   │
    │ 表格：操作 / 文件名 / 大小 / 进度 / 速度 / 画质 / 状态 │
    └───────────────────────────────────────────────────────┘
    ┌ 日志面板（可折叠，默认收起成一行摘要）─────────────────┐

为什么加筛选：任务一多，「失败的在哪」只能靠肉眼在长表里找；分段控件把
统计从文字变成可点的筛选项，统计与操作合一（文字仍然保留在分段项里）。

⚠️ 表格列宽禁忌（历史事故）：文件名列 Stretch 时右侧**绝不能有
ResizeToContents 列**，否则进度条会随文本变化左右漂移。这里沿用固定列宽；
「操作」列固定在最左，任何窗口宽度下都可见（2026-10-14 用户报看不见取消键）。
"""
import logging
import os
import shutil

from PySide6.QtCore import QTimer, QUrl, Qt, Signal
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QTextCharFormat,
                           QTextCursor)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QHBoxLayout,
                               QHeaderView, QLabel, QSizePolicy,
                               QTableWidgetItem, QWidget)
from qfluentwidgets import (FluentIcon as FIF, InfoBar, InfoBarPosition,
                            MessageBox, PlainTextEdit, ProgressBar, PushButton,
                            ScrollArea, TableWidget, TransparentToolButton)

from ..core import applog
from ..core.models import DownloadTaskInfo, fmt_size, fmt_speed, quality_html
from . import design as D
from . import workspace as W
from .perf import ProgressThrottle
from ..core.applog import get_logger

# ⚠️ 「操作」列必须放在**最左边**，不能放最右。
# 原因（2026-10-14 用户报「看不见取消按键」）：固定列宽合计 1150px，而主窗口
# 默认只有 1100px —— 放最右时「操作」列被推到视口外，默认就看不见。
# 移到最左后，无论窗口多窄、横向怎么滚动，取消/重试/打开/删除始终可见。
COL_ACTION, COL_NAME, COL_SIZE, COL_PROGRESS, COL_SPEED, COL_QUALITY, \
    COL_STATUS = range(7)

# 各列固定宽度（px）。**必须固定，不能用 ResizeToContents**：
# 文件名列是 Stretch，右侧任何一列随文本变宽变窄，都会把 Stretch 列挤窄，
# 导致进度条在整个下载过程中水平位置持续漂移（用户 2026-10-04 反馈）。
_COL_WIDTHS = {
    COL_ACTION: 180,     # 固定在最左；图集任务额外提供阅读按钮（测试守这条）
    COL_SIZE: 112,       # 实测「120/300 张」=126px（含单元格左右内距）
    COL_PROGRESS: 132,   # 进度条（108）+ 左右留白
    COL_SPEED: 118,      # 实测「99.9 MB/s」=126px 的 0.9 倍（正常值 6.0 MB/s 仅 74px）
    COL_QUALITY: 220,    # 实测「1080p · 8200kbps · 60fps」=272px，超出省略（完整值见 tooltip）
    COL_STATUS: 88,      # 实测状态文字 42px（含内距），失败原因走 tooltip
}
# 固定列合计 812px。默认窗口 1280 宽时内容区约 1000px，文件名列仍能拿到
# 约 190px 的弹性宽度；更窄的窗口靠横向滚动条，核心的「操作」列固定在
# 最左始终可见（见文件头的历史事故说明）。

# 日志面板：轮询间隔 / 面板保留行数 / 打开页面时先回看多少行
LOG_POLL_MS = 400
LOG_VIEW_LINES = applog.MEMORY_LINES      # 与内存缓冲对齐：再大也取不到更多
LOG_START_LINES = 200
_LOG_COLORS = {
    logging.WARNING: "#D97706",
    logging.ERROR: "#E5484D",
    logging.CRITICAL: "#E5484D",
}

# 筛选分段：索引与状态集合对应（0 = 全部）
_FILTERS = [
    ("全部", None),
    ("进行中", ("下载中", "等待中", "合并中")),
    ("已完成", ("已完成",)),
    ("失败", ("失败", "已取消")),
]

log = get_logger("task")


class TaskInterface(ScrollArea):
    read_manga = Signal(str)

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("taskInterface")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self.view = QWidget(self)
        self.view.setObjectName("taskView")
        self.setWidget(self.view)
        self.vBox = W.page_body(self.view, pad=24, spacing=12)

        self._rows = {}   # task_id -> row
        self._filter = 0
        # 进度更新节流：把高频的进度信号合并成低频刷新，避免 UI 线程被压满
        self._throttle = ProgressThrottle(120, self)
        self._throttle.set_handler(self._apply_updates)

        self._build_header()
        self._build_table()
        self._build_log_card()

        # 日志面板：先回看最近若干行，之后按固定间隔轮询内存日志（见 applog.MemoryHandler）
        self._log_seq, lines = applog.snapshot(LOG_START_LINES)
        self._append_logs(lines)
        self._log_timer = QTimer(self)
        self._log_timer.setInterval(LOG_POLL_MS)
        self._log_timer.timeout.connect(self._drain_logs)
        self._log_timer.start()

        m = self.ctx.manager
        m.task_added.connect(self._on_task_added)
        m.task_progress.connect(self._on_task_update)
        m.task_finished.connect(self._on_task_update)
        self._init_style()
        # 启动时就按"当前没有任务"摆好显隐与计数（否则首屏会是一张空表头）
        self._refresh_summary()

    def showEvent(self, event):
        """切回本页时立刻补一次日志，别等到下一个轮询周期"""
        super().showEvent(event)
        self._drain_logs()

    def _init_style(self):
        self.view.setStyleSheet("QWidget#taskView { background: transparent; }")

    # ------------------------------------------------------------------ #
    def _build_header(self):
        head = QHBoxLayout()
        head.setSpacing(D.GAP_SM)
        self.heading = W.PageHeading(
            "下载任务",
            "队列进度与失败重试都在这里；底部日志记录解析与下载的关键节点。",
            self.view)
        head.addWidget(self.heading, 1)
        self.openBtn = PushButton(FIF.FOLDER, "打开目录", self.view)
        self.openBtn.setToolTip("打开下载目录（也可以点窗口底部的状态条）")
        self.openBtn.clicked.connect(self._open_download_dir)
        self.retryFailedBtn = PushButton(FIF.SYNC, "重试失败", self.view)
        self.retryFailedBtn.clicked.connect(self._retry_failed)
        self.clearBtn = PushButton(FIF.DELETE, "清空已完成", self.view)
        self.clearBtn.clicked.connect(self._clear_finished)
        head.addWidget(self.openBtn, 0, Qt.AlignBottom)
        head.addWidget(self.retryFailedBtn, 0, Qt.AlignBottom)
        head.addWidget(self.clearBtn, 0, Qt.AlignBottom)
        self.vBox.addLayout(head)

    def _build_table(self):
        card = W.Panel(self.view, padding=(16, 12, 16, 12), spacing=10)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(D.GAP_SM)
        self.summaryLabel = W.text_label("暂无任务", card, D.TEXT_PRIMARY,
                                         D.FONT_BODY, D.W_SEMI)
        toolbar.addWidget(self.summaryLabel)
        toolbar.addSpacing(D.GAP_SM)
        self.filterTabs = W.SegmentedTabs([name for name, _ in _FILTERS], card,
                                          height=28, min_width=64)
        self.filterTabs.changed.connect(self._on_filter_changed)
        toolbar.addWidget(self.filterTabs)
        toolbar.addStretch(1)
        card.body.addLayout(toolbar)

        self.table = TableWidget(card)
        self.table.setColumnCount(7)
        # 表头顺序必须与上面的列常量顺序一致（操作在最左）
        self.table.setHorizontalHeaderLabels(
            ["操作", "文件名", "大小", "进度", "速度", "画质", "状态"])
        self.table.verticalHeader().hide()
        self.table.setBorderVisible(False)
        self.table.setBorderRadius(D.RADIUS_MD)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        # 单行 + 省略号：文件名很长时折行会把行高撑成两三行，整张表跟着抖
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        # 只让文件名列吸收剩余宽度；其余列全部固定，避免下载中文本变化
        # 引起列宽抖动、把进度条挤得左右乱跑（见 _COL_WIDTHS 说明）。
        header.setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        for c in (COL_ACTION, COL_SIZE, COL_PROGRESS, COL_SPEED, COL_QUALITY,
                  COL_STATUS):
            header.setSectionResizeMode(c, QHeaderView.Fixed)
            self.table.setColumnWidth(c, _COL_WIDTHS[c])
        self.table.setMinimumHeight(360)
        self.table.setMinimumWidth(0)
        # 横向滚动条**按需显示**：固定列合计仍可能超过视口，最右的「状态」列
        # 会被挤出去；核心操作（操作列）固定在最左，不受影响。
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        card.body.addWidget(self.table, 1)

        self.emptyLabel = W.EmptyState(
            FIF.DOWNLOAD, "还没有下载任务",
            "从「视频解析」或「批量下载」开始，任务会出现在这里。", card)
        self.emptyLabel.hide()
        card.body.addWidget(self.emptyLabel)
        self.vBox.addWidget(card, 1)

    def _build_log_card(self):
        """运行日志面板：解析/下载的关键节点都在这儿，出问题不用再去翻文件"""
        card = W.Panel(self.view, padding=(16, 10, 16, 12), spacing=8)

        head = QHBoxLayout()
        head.setSpacing(D.GAP_SM)
        self.logTitle = W.PanelTitle("运行日志", card)
        head.addWidget(self.logTitle, 1)
        # 这里只显示相对路径，完整路径放 tooltip：标签太长会把右边的按钮挤出窗口
        path = applog.log_path()
        self.logHint = W.text_label(
            f"{os.path.join('logs', 'app.log')}" if path else "日志未启用",
            card, D.TEXT_TERTIARY, D.FONT_CAPTION)
        self.logHint.setToolTip(
            f"完整路径：{path}\n下载与解析的关键节点都记在这里（面板只保留最近 "
            f"{LOG_VIEW_LINES} 行，文件里是全量的）" if path
            else "程序目录不可写，日志没有启用")
        head.addWidget(self.logHint)

        self.copyLogBtn = PushButton(FIF.COPY, "复制", card)
        self.copyLogBtn.setToolTip("把面板里的日志复制到剪贴板")
        self.copyLogBtn.clicked.connect(self._copy_log)
        self.clearLogBtn = PushButton(FIF.DELETE, "清空显示", card)
        self.clearLogBtn.setToolTip("只清空这里的显示，不影响 logs/app.log 文件")
        self.clearLogBtn.clicked.connect(self._clear_log_view)
        self.openLogBtn = PushButton(FIF.DOCUMENT, "打开文件", card)
        self.openLogBtn.clicked.connect(self._open_log_file)
        self.toggleLogBtn = TransparentToolButton(FIF.UP, card)
        self.toggleLogBtn.setToolTip("收起 / 展开日志面板")
        self.toggleLogBtn.clicked.connect(self._toggle_log)
        for btn in (self.copyLogBtn, self.clearLogBtn, self.openLogBtn):
            head.addWidget(btn)
        head.addWidget(self.toggleLogBtn)
        card.body.addLayout(head)

        self.logView = PlainTextEdit(card)
        self.logView.setReadOnly(True)
        # 自动折行：日志里有几百字符的 URL，qfluentwidgets 的平滑滚动条会把原生横向
        # 滚动条关掉（SmoothScrollDelegate），不折行就只能看前半截。
        # 横向尺寸策略设成 Ignored，免得长行把外层页面撑宽、把右边按钮挤出窗口。
        self.logView.setLineWrapMode(PlainTextEdit.WidgetWidth)
        self.logView.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        # 超过上限自动丢最旧的行：挂一整天也不会越滚越卡
        self.logView.setMaximumBlockCount(LOG_VIEW_LINES)
        self.logView.setMinimumHeight(150)
        self.logView.setMaximumHeight(220)
        self.logView.setPlaceholderText("暂无日志")
        font = QFont("Consolas")
        font.setStyleHint(QFont.Monospace)
        font.setPointSize(9)
        self.logView.setFont(font)
        card.body.addWidget(self.logView)
        self.logCard = card
        self.vBox.addWidget(card)

    def _toggle_log(self):
        shown = not self.logView.isVisible()
        self.logView.setVisible(shown)
        self.logHint.setVisible(shown)
        for btn in (self.copyLogBtn, self.clearLogBtn, self.openLogBtn):
            btn.setVisible(shown)
        self.toggleLogBtn.setIcon(FIF.UP if shown else FIF.DOWN)
        self.toggleLogBtn.setToolTip("收起日志面板" if shown else "展开日志面板")

    # ------------------------------------------------------------------ #
    # 日志面板
    def _drain_logs(self):
        if not self.isVisible():
            return                      # 不在本页就不刷，别白白占 UI 线程
        seq, lines = applog.fetch_since(self._log_seq)
        if not lines:
            return
        self._log_seq = seq
        self._append_logs(lines)

    def _append_logs(self, lines):
        if not lines:
            return
        bar = self.logView.verticalScrollBar()
        # 停在底部就继续跟随最新；往上翻看历史时别把他拽回去
        follow = bar.value() >= bar.maximum() - 4
        cursor = self.logView.textCursor()
        cursor.movePosition(QTextCursor.End)
        for level, text in lines:
            fmt = QTextCharFormat()
            color = _LOG_COLORS.get(level)
            if color:
                fmt.setForeground(QColor(color))
            cursor.insertText(text + "\n", fmt)
        self.logView.setTextCursor(cursor)
        if follow:
            bar.setValue(bar.maximum())

    def _copy_log(self):
        text = self.logView.toPlainText()
        if not text.strip():
            self._toast("没有日志", "日志面板当前是空的", "warning")
            return
        QApplication.clipboard().setText(text)
        self._toast("已复制", f"日志已复制到剪贴板（{len(text)} 字符）", "success")

    def _clear_log_view(self):
        self.logView.clear()
        applog.clear_memory()
        # 缓冲清空后序号还在递增：同步一次，免得把清掉之前的行又补回来
        self._log_seq, _ = applog.snapshot(0)

    def _open_log_file(self):
        path = applog.log_path()
        if not path or not os.path.exists(path):
            self._toast("日志文件不存在", "程序目录不可写，日志没有启用", "warning")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    # ------------------------------------------------------------------ #
    def _on_task_added(self, info: DownloadTaskInfo):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._rows[info.task_id] = row

        name = QTableWidgetItem(info.name)
        name.setToolTip(info.save_path)
        name.setData(Qt.UserRole, info.task_id)   # 行 → 任务 的锚点，删行/对齐靠它
        self.table.setItem(row, COL_NAME, name)
        self.table.setItem(row, COL_SIZE, QTableWidgetItem("--"))

        # 进度条放进容器：固定宽度 + 左对齐，位置不随数值/列宽变化
        bar = ProgressBar(self.table)
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setFixedWidth(116)
        holder = QWidget(self.table)
        hb = QHBoxLayout(holder)
        hb.setContentsMargins(8, 0, 8, 0)
        hb.setSpacing(0)
        hb.addWidget(bar)
        hb.addStretch(1)
        self.table.setCellWidget(row, COL_PROGRESS, holder)

        self.table.setItem(row, COL_SPEED, QTableWidgetItem("--"))
        self.table.setCellWidget(row, COL_QUALITY, self._quality_widget(info.quality))
        self.table.setItem(row, COL_STATUS, QTableWidgetItem(info.status))

        actions = QWidget(self.table)
        h = QHBoxLayout(actions)
        h.setContentsMargins(4, 0, 4, 0)
        h.setSpacing(2)
        cancelBtn = TransparentToolButton(FIF.CLOSE, actions)
        cancelBtn.setToolTip("取消")
        cancelBtn.clicked.connect(lambda _, tid=info.task_id: self.ctx.manager.cancel(tid))
        retryBtn = TransparentToolButton(FIF.SYNC, actions)
        retryBtn.setToolTip("重试")
        # 传**当前**下载目录：用户改过目录后重试要落到新目录，
        # 否则任务沿用创建时的旧 save_path，表现为「改了目录还是下到老地方」。
        retryBtn.clicked.connect(
            lambda _, tid=info.task_id: self.ctx.manager.retry(
                tid, self.ctx.config.get("download_path")))
        openBtn = TransparentToolButton(FIF.FOLDER, actions)
        openBtn.setToolTip("打开所在文件夹")
        openBtn.clicked.connect(
            lambda _, p=info.save_path: self._open_file_location(p))
        delBtn = TransparentToolButton(FIF.DELETE, actions)
        delBtn.setToolTip("删除下载的文件")
        delBtn.clicked.connect(
            lambda _, tid=info.task_id: self._on_delete_file(tid))
        h.addWidget(cancelBtn)
        h.addWidget(retryBtn)
        h.addWidget(openBtn)
        if self._manga_task_folder(info):
            readBtn = TransparentToolButton(FIF.PHOTO, actions)
            readBtn.setToolTip("阅读漫画（下载完成后可用）")
            readBtn.clicked.connect(lambda _, tid=info.task_id: self._read_manga_task(tid))
            h.addWidget(readBtn)
        h.addWidget(delBtn)
        self.table.setCellWidget(row, COL_ACTION, actions)
        self._apply_row_filter(row, info)
        self._refresh_summary()

    def _progress_bar_at(self, row: int):
        """取某行的进度条控件（它被包在容器里，用于锁死水平位置）"""
        holder = self.table.cellWidget(row, COL_PROGRESS)
        if holder is None:
            return None
        if isinstance(holder, ProgressBar):
            return holder
        return holder.findChild(ProgressBar)

    def _quality_widget(self, quality: str):
        """「画质」单元格：分辨率/码率 + 高亮帧率（60fps 绿色、30fps 灰色）"""
        label = QLabel(self.table)
        label.setContentsMargins(6, 0, 6, 0)
        if not quality:
            label.setText("--")
            label.setToolTip("该来源不提供多档清晰度")
            return label
        label.setText(quality_html(quality))
        label.setTextFormat(Qt.RichText)
        label.setToolTip(f"下载所用档位：{quality}")
        return label

    def _on_task_update(self, info: DownloadTaskInfo):
        """高频信号入口：只入队，由节流器合并后再真正刷 UI"""
        self._throttle.push(info.task_id, info)

    def _apply_updates(self, batch):
        """节流后的批量刷新（每 120ms 最多一次）"""
        for info in batch:
            row = self._rows.get(info.task_id)
            if row is None:
                continue
            size_item = self.table.item(row, COL_SIZE)
            if size_item:
                # 逐张下载的站点（禁漫）把「张数」写进 downloaded/total
                # （见 downloader._run_jmcomic 的 on_progress），
                # 那是张数不是字节 —— 用 fmt_size 格式化会显示成「91 B」。
                if (info.url or "").startswith("jmcomic://"):
                    txt = f"{info.downloaded}/{info.total} 张" if info.total else "--"
                elif info.total:
                    txt = fmt_size(info.total)
                else:
                    txt = size_item.text() or "--"
                if size_item.text() != txt:
                    size_item.setText(txt)
            bar = self._progress_bar_at(row)
            if bar and bar.value() != info.percent:
                bar.setValue(info.percent)
            speed_item = self.table.item(row, COL_SPEED)
            if speed_item:
                txt = fmt_speed(info.speed) if info.speed > 0 else "--"
                if speed_item.text() != txt:
                    speed_item.setText(txt)
            status_item = self.table.item(row, COL_STATUS)
            if status_item:
                # 状态列只写状态本身（列只有 84px），错误原因放 tooltip + 摘要下方
                text = info.status
                if status_item.text() != text:
                    status_item.setText(text)
                    status_item.setToolTip(
                        f"失败原因：{info.error}" if info.error else info.status)
            self._apply_row_filter(row, info)
        self._refresh_summary()

    def _refresh_summary(self):
        """摘要 + 分段计数：任务状态变化时才重算，避免每次进度更新都全量遍历"""
        tasks = self.ctx.manager.all_tasks()
        count = len(tasks)
        # 没有任务时连表头一起收起来：空表头会让"空"看起来像是加载失败。
        # 用 isHidden() 而不是 isVisible()：页面还没被切到、窗口还没 show 时
        # isVisible() 恒为 False，拿它判断会把"该隐藏"误判成"已经隐藏"。
        show_table = count > 0
        if self.table.isHidden() != (not show_table):
            self.table.setVisible(show_table)
        if self.emptyLabel.isHidden() != show_table:
            self.emptyLabel.setVisible(not show_table)
        counts = []
        for _, statuses in _FILTERS:
            if statuses is None:
                counts.append(count)
            else:
                counts.append(sum(1 for t in tasks if t.status in statuses))
        labels = [f"{name} {n}" for (name, _), n in zip(_FILTERS, counts)]
        for i, label in enumerate(labels):
            self.filterTabs.set_item_text(i, label)
        self.filterTabs.setMinimumWidth(76 * len(labels))
        done, running, failed = counts[2], counts[1], counts[3]
        if not tasks:
            text = "暂无任务"
        else:
            text = f"共 {count} 个任务"
            self.heading.set_meta(
                f"进行中 {running} · 已完成 {done} · 失败 {failed}"
                "　｜　运行日志记录解析与下载的关键节点")
        if self.summaryLabel.text() != text:
            self.summaryLabel.setText(text)
        self.retryFailedBtn.setEnabled(failed > 0)
        self.clearBtn.setEnabled(done + failed > 0)

    def _on_filter_changed(self, index: int):
        self._filter = index
        for row in range(self.table.rowCount()):
            item = self.table.item(row, COL_NAME)
            tid = item.data(Qt.UserRole) if item is not None else None
            info = next((t for t in self.ctx.manager.all_tasks()
                         if t.task_id == tid), None)
            if info is not None:
                self._apply_row_filter(row, info)
            else:
                self.table.setRowHidden(row, index != 0)

    def _apply_row_filter(self, row: int, info: DownloadTaskInfo):
        """按当前分段筛选决定这一行是否显示（隐藏而不是删行，切换零成本）"""
        statuses = _FILTERS[self._filter][1]
        hidden = statuses is not None and info.status not in statuses
        if self.table.isRowHidden(row) != hidden:
            self.table.setRowHidden(row, hidden)

    def _open_download_dir(self):
        path = self.ctx.config.get("download_path")
        os.makedirs(path, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _manga_task_folder(self, info):
        """仅图集可以整夹阅读；视频封面不能把整个下载根目录变成一本书。"""
        from ..core.manga import folder_key, is_page
        path = info.save_path
        if not path:
            return None
        # 保留历史整本任务的路径语义，即使目录已被移走也不能退到父目录。
        if (info.url or "").startswith(("jmcomic://", "ehentai://")):
            return path
        stem = os.path.splitext(os.path.basename(path))[0].casefold()
        if not is_page(path) or info.name == "封面" or stem.endswith("_cover"):
            return None
        folder = os.path.dirname(os.path.abspath(path))
        download_root = self.ctx.config.get("download_path")
        if download_root and folder_key(folder) == folder_key(download_root):
            return None
        return folder

    def _read_manga_task(self, task_id):
        from ..core.manga import folder_key
        tasks = self.ctx.manager.all_tasks()
        info = next((task for task in tasks if task.task_id == task_id), None)
        if info is None or info.status != "已完成":
            self._toast("尚未下载完成", "请等待漫画 / 图集下载完成后再阅读", "warning")
            return
        folder = self._manga_task_folder(info)
        if not folder:
            self._toast("不是漫画图集", "封面或下载根目录中的单张图片不支持整夹阅读", "warning")
            return
        if not (info.url or "").startswith(("jmcomic://", "ehentai://")):
            # 普通图集按图片建任务。WEBP 等同夹任务也要完成，避免页表仅为半本快照。
            if any(task.save_path and
                   folder_key(os.path.dirname(os.path.abspath(task.save_path))) == folder_key(folder)
                   and task.status != "已完成" for task in tasks):
                self._toast("图集尚未下载完成", "请等待同一图集的所有图片下载完成后再阅读", "warning")
                return
        self.read_manga.emit(folder)

    def _open_file_location(self, path: str):
        """在资源管理器里定位文件；路径不存在时给出可操作提示而不是静默无反应。"""
        if not path:
            self._toast("无法打开", "该任务没有记录保存路径", "warning")
            return
        target = path if os.path.isdir(path) else os.path.dirname(path)
        if not target:
            self._toast("无法打开", "保存路径无效", "warning")
            return
        if not os.path.isdir(target):
            # 目录被删/被移走是常见情况（用户自己整理过），要说清楚而不是点了没动静
            self._toast("目录不存在",
                        f"找不到：{target}\n文件可能已被移动或删除", "warning")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(target))

    @staticmethod
    def _related_files(path: str) -> list:
        """与主文件配套的文件（如封面 `<同名>_cover.jpg`）"""
        if not path:
            return []
        root, _ = os.path.splitext(path)
        cover = root + "_cover.jpg"
        return [cover] if os.path.exists(cover) else []

    def _on_delete_file(self, task_id: str):
        """删除该任务已下载的文件，并清除对应的任务记录"""
        mgr = self.ctx.manager
        info = next((t for t in mgr.all_tasks() if t.task_id == task_id), None)
        if info is None:
            # 记录已经不在了（异常情况下遗留的行）：清掉这行并给个反馈，别静默无反应
            QTimer.singleShot(0, self._prune_rows)
            self._toast("任务已不存在", "这条任务记录已经被清除了", "warning")
            return
        if info.status in ("下载中", "等待中", "合并中"):
            self._toast("无法删除", "任务正在进行，请先取消任务再删除文件", "warning")
            return

        path = info.save_path or ""
        exists = bool(path) and os.path.exists(path)
        is_dir = exists and os.path.isdir(path)
        extras = [] if is_dir else self._related_files(path)
        if exists:
            size = "文件夹" if is_dir else fmt_size(os.path.getsize(path))
            extra_txt = (f"\n同时删除 {len(extras)} 个关联文件（封面）"
                         if extras else "")
            box = MessageBox(
                "删除文件",
                (f"确定要删除已下载的文件吗？\n\n{os.path.basename(path)}"
                 f"\n大小 {size}{extra_txt}\n\n此操作不可撤销，任务记录会一并清除。"),
                self.window())
        else:
            box = MessageBox("文件已不存在",
                             "本地文件已不在原位置，是否仅清除这条任务记录？",
                             self.window())
        if not box.exec():
            return

        # 先发取消信号，避免下载线程继续写这个文件
        mgr.cancel(task_id)
        removed, failed = [], []
        for p in ([path] if exists else []) + extras:
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p)
                else:
                    os.remove(p)
                removed.append(p)
            except OSError as e:
                failed.append(f"{os.path.basename(p)}：{e}")
        # 从任务表移除（只允许清除已结束的任务）
        if mgr.remove_tasks([task_id]):
            self._throttle._pending.clear()
            # 此时还在这个按钮自己的点击事件里，删行要等事件走完
            QTimer.singleShot(0, self._prune_rows)
        self._refresh_summary()

        if failed:
            log.warning("删除文件（含封面）失败 %s：%s", task_id, "；".join(failed))
            self._toast("部分删除失败", "；".join(failed)[:150], "error")
        elif removed:
            log.info("已删除下载文件 %s：%s", task_id,
                     "；".join(os.path.basename(p) for p in removed))
            self._toast("已删除", f"已删除 {len(removed)} 个文件", "success")
        else:
            log.info("文件已不存在，仅清除记录 %s", task_id)
            self._toast("已清除记录", "文件不存在，仅清除了任务记录", "warning")

    def _toast(self, title: str, content: str, kind: str):
        fn = {"success": InfoBar.success, "error": InfoBar.error,
              "warning": InfoBar.warning}.get(kind, InfoBar.info)
        fn(title, content, orient=Qt.Horizontal, isClosable=True,
           position=InfoBarPosition.TOP, duration=3000, parent=self)

    def _clear_finished(self):
        count = len(self.ctx.manager.finished_tasks())
        if not count:
            return
        w = MessageBox("清空记录", f"确定要清除 {count} 条已结束的任务记录吗？"
                                 "（不会删除已下载的文件）", self.window())
        if not w.exec():
            return
        if not self.ctx.manager.clear_finished():
            return
        self._throttle._pending.clear()
        self._prune_rows()
        self._refresh_summary()
        log.info("清空已结束的任务记录 %d 条", count)

    def _retry_failed(self):
        # 同样传当前目录：批量重试失败任务也要跟随用户改过的下载目录
        n = self.ctx.manager.retry_tasks(
            rebase_to=self.ctx.config.get("download_path"))
        if n:
            self._toast("重新入队", f"{n} 个失败任务已重新加入队列", "success")
        else:
            self._toast("无需重试", "当前没有失败的任务", "warning")

    def _sync_rows(self):
        """按表格的实际行序重建 task_id → row 映射（增删行后必须调用）

        注意**不要**拿 `manager.all_tasks()` 的顺序来推行号：任务表里的行
        只增不减/或删除后行号会串位，两者顺序并不一致（映射一乱，进度就会
        画到别的行上，删除按钮也会点到「已经没有记录的行」而毫无反应）。
        """
        self._rows = {}
        for row in range(self.table.rowCount()):
            item = self.table.item(row, COL_NAME)
            tid = item.data(Qt.UserRole) if item is not None else None
            if tid:
                self._rows[tid] = row

    def _prune_rows(self):
        """删掉已经没有对应任务记录的行（幽灵行），并对齐行号映射"""
        alive = {t.task_id for t in self.ctx.manager.all_tasks()}
        for row in range(self.table.rowCount() - 1, -1, -1):
            item = self.table.item(row, COL_NAME)
            tid = item.data(Qt.UserRole) if item is not None else None
            if not tid or tid not in alive:
                self.table.removeRow(row)
        self._sync_rows()
