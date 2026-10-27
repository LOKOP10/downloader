# -*- coding: utf-8 -*-
"""本地视频库页面：卡片墙 + 续播进度，结构对齐 manga_interface.py。"""
import logging
import os
from collections import deque

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, QUrl, Slot
from PySide6.QtGui import (QColor, QDesktopServices, QPainter, QPainterPath, QPen,
                           QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout, QListView,
                               QListWidget, QListWidgetItem, QMenu, QMessageBox,
                               QStyle, QStyledItemDelegate, QWidget)
from qfluentwidgets import ComboBox, FluentIcon as FIF, PrimaryPushButton, PushButton, SearchLineEdit
from qfluentwidgets.common.style_sheet import isDarkTheme

from ..core.ffmpeg import ffmpeg_path
from ..core.video_library import (VideoLibrary, download_cover_image, extract_first_frame,
                                  find_downloaded_cover, folder_key, is_video,
                                  is_video_file_entry, natural_key, scan_videos)
from . import design as D
from . import workspace as W
from .manga_reader import LocalJobs
from .video_player import VideoPlayer

log = logging.getLogger("vdl.video")


# 卡片右上角删除按钮：鼠标悬停该卡片时才出现，点它走与右键一致的删除确认。
_DELETE_SIZE = 22


def _card_rect(cell):
    """卡片内容区（与 delegate 的 8px 内缩保持一致）。"""
    return QRectF(cell).adjusted(8, 8, -8, -8)


def _delete_rect(cell):
    """删除按钮几何：封面右上角。delegate 负责画，视频墙负责判命中，共用一份计算。"""
    rect = _card_rect(cell)
    return QRectF(rect.right() - _DELETE_SIZE - 6, rect.top() + 6,
                  _DELETE_SIZE, _DELETE_SIZE)


class _PosterDelegate(QStyledItemDelegate):
    """视频卡片：封面海报 + 时长徽章 + 标题 + 续播进度条 + 悬停删除按钮。"""

    def __init__(self, covers, view=None, parent=None):
        # covers: {路径: QPixmap|None} 由页面持有并刷新，delegate 只读。
        # view: 视频墙本身，只用来读悬停行与忙碌状态，delegate 自己不存状态。
        super().__init__(parent)
        self._covers = covers
        self._view = view

    def paint(self, painter, option, index):
        item = index.data(Qt.ItemDataRole.UserRole)
        if item is None:
            return
        dark = isDarkTheme()
        primary = QColor(D.TEXT_PRIMARY[int(dark)])
        muted = QColor(D.TEXT_SECONDARY[int(dark)])
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = _card_rect(option.rect)
        poster_rect = QRectF(rect.left(), rect.top(), rect.width(), 128)
        clip = QPainterPath()
        clip.addRoundedRect(poster_rect, 10, 10)
        painter.fillPath(clip, QColor(D.BG_TERTIARY[int(dark)]))
        pixmap = self._covers.get(item.cover) if item.cover else None
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            painter.save()
            painter.setClipPath(clip)
            # KeepAspectRatioByExpanding 铺满海报区并居中裁掉溢出（对齐漫画封面画法）
            scaled = pixmap.scaled(poster_rect.size().toSize(),
                                   Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                   Qt.TransformationMode.SmoothTransformation)
            left = (scaled.width() - int(poster_rect.width())) // 2
            top = (scaled.height() - int(poster_rect.height())) // 2
            painter.drawPixmap(poster_rect.toRect(),
                               scaled.copy(left, top, int(poster_rect.width()), int(poster_rect.height())))
            painter.restore()
        else:
            painter.setPen(muted)
            painter.setFont(D.font(16))
            painter.drawText(poster_rect, Qt.AlignmentFlag.AlignCenter, "本地视频")
        badge = QRectF(rect.left() + 9, rect.top() + 99, 76, 22)
        painter.setBrush(QColor(0, 0, 0, 180))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(badge, 5, 5)
        painter.setPen(QColor("#FFFFFF"))
        painter.setFont(D.font(11))
        from ..core.video_library import fmt_time
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter,
                         fmt_time(item.duration_ms) if item.duration_ms else "未播放")
        painter.setPen(primary)
        painter.setFont(D.font(14, D.W_SEMI))
        title = painter.fontMetrics().elidedText(item.title, Qt.TextElideMode.ElideRight, int(rect.width()))
        painter.drawText(QRectF(rect.left(), rect.top() + 140, rect.width(), 24), title)
        painter.setPen(muted)
        painter.setFont(D.font(11))
        progress = (f"上次看到 {fmt_time(item.resume_ms)}"
                    if item.resume_ms > 0 else ("已看完" if item.watched else "还没开始看"))
        painter.drawText(QRectF(rect.left(), rect.top() + 168, rect.width(), 20), progress)
        painter.fillRect(QRectF(rect.left(), rect.top() + 194, rect.width(), 3), QColor(D.BORDER[int(dark)]))
        if item.resume_ms and item.duration_ms:
            painter.fillRect(QRectF(rect.left(), rect.top() + 194, rect.width() * item.percent / 100, 3),
                             QColor(D.TEXT_ACCENT[int(dark)]))
        if option.state & QStyle.StateFlag.State_Selected:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QColor(D.TEXT_ACCENT[int(dark)]))
            painter.drawRoundedRect(rect.adjusted(-3, -3, 3, 5), 10, 10)
        view = self._view
        if (view is not None and not getattr(view, "busy", False)
                and getattr(view, "hover_row", -1) == index.row()):
            # 只在鼠标所在卡片上露出删除按钮：卡片墙常态下不该满屏叉号
            button = _delete_rect(option.rect)
            hot = bool(getattr(view, "delete_hovered", False))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(D.DANGER[int(dark)]) if hot else QColor(0, 0, 0, 175))
            painter.drawEllipse(button)
            painter.setPen(QPen(QColor("#FFFFFF"), 1.6))
            inset = 6.5
            painter.drawLine(QPointF(button.left() + inset, button.top() + inset),
                             QPointF(button.right() - inset, button.bottom() - inset))
            painter.drawLine(QPointF(button.right() - inset, button.top() + inset),
                             QPointF(button.left() + inset, button.bottom() - inset))
        painter.restore()


class _VideoShelf(QListWidget):
    """视频墙：悬停露出右上角删除按钮，点按钮删除，点其它位置继续播放。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.hover_row = -1           # 鼠标所在卡片行号（-1 = 不在任何卡片上）
        self.delete_hovered = False   # 鼠标是否正压在删除按钮上
        self.busy = False             # 与页面 _busy 同步，忙碌时不响应删除
        self.on_delete = None         # callable(QListWidgetItem)

    def _delete_item_at(self, position):
        """返回被点中的卡片；点不在删除按钮上则返回 None。"""
        item = self.itemAt(position)
        if item is None:
            return None
        if _delete_rect(self.visualItemRect(item)).contains(QPointF(position)):
            return item
        return None

    def mouseMoveEvent(self, event):
        point = event.position().toPoint()
        hovered = self.itemAt(point)
        row = self.row(hovered) if hovered is not None else -1
        hot = self._delete_item_at(point) is not None and not self.busy
        if (row, hot) != (self.hover_row, self.delete_hovered):
            self.hover_row, self.delete_hovered = row, hot
            self.viewport().update()
        self.setCursor(Qt.CursorShape.PointingHandCursor if hot else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self.hover_row != -1 or self.delete_hovered:
            self.hover_row, self.delete_hovered = -1, False
            self.viewport().update()
        self.unsetCursor()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self.busy:
            item = self._delete_item_at(event.position().toPoint())
            if item is not None:
                # 吃掉这次点击：否则会继续冒泡成 itemClicked，又把视频播起来
                event.accept()
                self.hover_row, self.delete_hovered = -1, False
                self.viewport().update()
                if callable(self.on_delete):
                    self.on_delete(item)
                return
        super().mousePressEvent(event)


class VideoInterface(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("videoInterface")
        self.setAcceptDrops(True)
        self.library = VideoLibrary(os.path.join(ctx.config.base_dir, "video_library.json"))
        self._busy = False
        self._closed = False
        self._auto_add_queue = deque()
        self._auto_add_keys = set()
        self._player = None
        self.jobs = LocalJobs(self)
        # 封面：路径 → QPixmap 的运行时缓存（含加载失败的负缓存 None），
        # 命中失败超过上限自动丢弃最旧的，行为对齐漫画封面的 _covers。
        self._covers = {}
        self._cover_pending = set()
        self._cover_generation = 0
        # 必须显式传程序目录：ffmpeg_path() 无参时回落到 ffmpeg.base_dir()，
        # 源码运行时那是仓库根（ffmpeg 实际在 dist/tools/），截帧会静默失败、
        # 卡片永远没有封面。与设置页的 ffmpeg 检测口径保持一致。
        self._ffmpeg = ffmpeg_path(ctx.config.base_dir)
        # 截帧缓存目录：程序目录/video_covers/（与 video_library.json 同级）
        self._covers_dir = os.path.join(ctx.config.base_dir, "video_covers")
        layout = W.page_body(self, pad=24, spacing=12)
        header = QHBoxLayout()
        header.setSpacing(D.GAP_SM)
        self.heading = W.PageHeading(
            "本地视频",
            "下载完成的视频自动入库 · 支持 MP4 / MKV / WebM · 记住播放位置，关掉再开接着看",
            self)
        header.addWidget(self.heading, 1)
        self.refreshBtn = PushButton(FIF.SYNC, "检查文件", self)
        self.refreshBtn.setToolTip(
            "核对每个视频的源文件是否还在：已被删除的记录自动清理，缺封面的补上。\n"
            "外置盘 / 网络盘暂时没连上时只提示，不会清掉记录。")
        self.refreshBtn.clicked.connect(self.refresh_files)
        self.moreBtn = PushButton(FIF.MORE, "更多", self)
        self.moreBtn.setToolTip("重新加载库、清理失效记录等")
        self.moreBtn.clicked.connect(self._more_menu)
        self.folderBtn = PushButton(FIF.FOLDER, "导入文件夹", self)
        self.folderBtn.setToolTip("递归扫描文件夹里的全部视频并入库")
        self.folderBtn.clicked.connect(self._choose_folder)
        self.addBtn = PrimaryPushButton(FIF.ADD, "添加视频", self)
        self.addBtn.setToolTip("选择一个或多个视频文件加入视频库")
        self.addBtn.clicked.connect(self._choose_video)
        for btn in (self.refreshBtn, self.moreBtn, self.folderBtn, self.addBtn):
            header.addWidget(btn, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(header)

        # 搜索 / 排序与视频墙同属一块面板：工具与内容是一体的
        panel = W.Panel(self, padding=(16, 10, 16, 12), spacing=10)
        tools = QHBoxLayout()
        tools.setSpacing(D.GAP_SM)
        self.search = SearchLineEdit(panel)
        self.search.setPlaceholderText("搜索视频名称")
        self.search.setMinimumWidth(200)
        self.search.textChanged.connect(self.refresh_shelf)
        tools.addWidget(self.search, 1)
        self.sort = ComboBox(panel)
        self.sort.addItems(["最近播放", "名称顺序", "观看进度"])
        self.sort.setMinimumWidth(120)
        self.sort.currentIndexChanged.connect(self.refresh_shelf)
        tools.addWidget(self.sort)
        panel.body.addLayout(tools)
        self.shelf = _VideoShelf(self)
        self.shelf.setViewMode(QListView.ViewMode.IconMode)
        self.shelf.setResizeMode(QListView.ResizeMode.Adjust)
        self.shelf.setMovement(QListView.Movement.Static)
        self.shelf.setWrapping(True)
        self.shelf.setGridSize(QSize(200, 230))
        self.shelf.setUniformItemSizes(True)
        self.shelf.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.shelf.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.shelf.setItemDelegate(_PosterDelegate(self._covers, self.shelf))
        self.shelf.on_delete = self._delete_from_card
        self.shelf.setAccessibleName("视频墙")
        self.shelf.itemClicked.connect(self.open_item)
        self.shelf.itemActivated.connect(self.open_item)
        self.shelf.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.shelf.customContextMenuRequested.connect(self._context_menu)
        D.theme_qss(self.shelf, "QListWidget{background:transparent;border:0;}",
                    "QListWidget{background:transparent;border:0;}")
        panel.body.addWidget(self.shelf, 1)
        self.empty = D.caption("", self)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setMinimumHeight(180)
        panel.body.addWidget(self.empty, 1)
        self.status = D.hint_label(
            "本地播放 · 源文件不变 · 悬停卡片点右上角 × 或右键可删除（可选仅移除记录，或连源文件一起删除）",
            self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        panel.body.addWidget(self.status)
        layout.addWidget(panel, 1)
        self.refresh_shelf()
        if self.library.warning:
            self.status.setText(self.library.warning)
        ctx.manager.task_finished.connect(self._queue_completed_video)
        # 启动后静默核对一次：源文件早被删掉的记录不该继续占着卡片
        QTimer.singleShot(600, self._prune_missing)

    # ------------------------------------------------------------------ 落盘
    def _save(self):
        try:
            self.library.save()
            return True
        except OSError as error:
            self.status.setText(f"视频记录保存失败：{error}")
            return False

    # ------------------------------------------------------------------ 自动入库
    @Slot(object)
    def _queue_completed_video(self, info):
        """下载完成的视频任务自动进视频库（jm/eh 图集交给漫画库）。"""
        if self._closed or info.status != "已完成":
            return
        if (info.url or "").startswith(("jmcomic://", "ehentai://")):
            return
        path = info.save_path
        if not path or not is_video_file_entry(path, info.name):
            return
        key = folder_key(path)
        if key not in self._auto_add_keys:
            self._auto_add_keys.add(key)
            # 封面此刻可能还没下完（封面是独立任务），入库时再逐级兜底；
            # cover_url 用于本地没有封面文件时直接下载解析时看到的封面。
            self._auto_add_queue.append((path, info.name or "", info.cover_url or ""))
        self._drain_auto_add()

    def _drain_auto_add(self):
        while not self._closed and not self._busy and self._auto_add_queue:
            path, title, cover_url = self._auto_add_queue.popleft()
            self._auto_add_keys.discard(folder_key(path))
            item = self.library.add(path, title)
            if item.cover:
                continue  # 重入库的老记录已有封面
            item.cover = find_downloaded_cover(path)
            self._save()
            if item.cover:
                self.refresh_shelf()
                continue
            # 网络下载封面 / 截帧都在工作线程做，完成后回 UI 线程补写并刷新
            self._fetch_cover_async(item, cover_url)
            log.info("视频自动入库：%s", item.title)

    def _fetch_cover_async(self, item, cover_url):
        """工作线程里按「下载解析封面 → 截第一帧」补封面，完成后落盘刷新。"""
        if self._closed:
            return
        generation = self._cover_generation
        ffmpeg = self._ffmpeg
        covers_dir = self._covers_dir

        def work():
            return (download_cover_image(cover_url, covers_dir, item.path)
                    or extract_first_frame(item.path, ffmpeg, covers_dir))

        def done(cover, error):
            if self._closed or generation != self._cover_generation:
                return
            if cover and not item.cover:
                item.cover = cover
                if self._save():
                    self.refresh_shelf()
                    self._load_cover(item)

        self.jobs.start(work, done)

    # ------------------------------------------------------------------ 导入
    def _choose_video(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择视频文件", self.ctx.config.get("download_path"),
            "视频文件 (*.mp4 *.mkv *.webm *.mov *.m4v *.avi *.flv *.ts *.mpg *.mpeg *.wmv *.3gp);;所有文件 (*.*)")
        if paths:
            self.import_paths(paths)

    def _choose_folder(self):
        path = QFileDialog.getExistingDirectory(
            self, "选择视频文件夹（递归扫描全部子目录）", self.ctx.config.get("download_path"))
        if path:
            self.import_paths([path])

    def import_paths(self, paths, read=False):
        if self._busy or self._closed:
            return
        self._set_busy(True)
        self.status.setText("正在扫描视频…")

        def scan():
            found = []
            for path in paths:
                if os.path.isdir(path):
                    for video in scan_videos(path):
                        found.append((video, ""))
                elif is_video(path):
                    found.append((path, ""))
            # 封面：解析下载的用同名 _cover 图；本地导入的截第一帧（ffmpeg
            # 子进程在工作线程跑，不卡 UI）。失败静默回退占位图。
            return [(video, title,
                     find_downloaded_cover(video)
                     or extract_first_frame(video, self._ffmpeg, self._covers_dir))
                    for video, title in found]

        def done(found, error):
            self._set_busy(False)
            if error:
                self.status.setText(f"扫描失败：{error}")
                return
            if not found:
                self.status.setText("没有找到视频文件。支持 MP4 / MKV / WebM / MOV / AVI / FLV / TS / WMV 等常见格式。")
                return
            for path, title, cover in found:
                item = self.library.add(path, title)
                if not item.cover and cover:
                    item.cover = cover
            saved = self._save()
            self.refresh_shelf()
            if saved:
                self.status.setText(f"已导入 / 更新 {len(found)} 个视频 · 播放进度自动保存")
            # 单选时直接播放（拖入单个文件 / 添加一个视频）。

        self.jobs.start(scan, done)

    def open_folder(self, folder):
        """Entry point for the completed-download action."""
        self.import_paths([folder])

    def _set_busy(self, busy):
        self._busy = busy
        for button in (self.addBtn, self.folderBtn, self.refreshBtn, self.moreBtn):
            button.setEnabled(not busy)
        self.shelf.busy = busy
        if not busy and not self._closed and self._auto_add_queue:
            self._drain_auto_add()

    # ------------------------------------------------------------------ 展示
    def refresh_shelf(self, *_):
        if self._closed:
            return
        query = self.search.text().strip().casefold()
        items = [v for v in self.library.videos if query in v.title.casefold()]
        mode = self.sort.currentIndex()
        if mode == 1:
            items.sort(key=lambda v: natural_key(v.title))
        elif mode == 2:
            items.sort(key=lambda v: v.percent if v.resume_ms else 0, reverse=True)
        else:
            items.sort(key=lambda v: v.last_played, reverse=True)
        self.shelf.clear()
        self.shelf.hover_row, self.shelf.delete_hovered = -1, False
        for item in items:
            widget_item = QListWidgetItem(item.title)
            widget_item.setData(Qt.ItemDataRole.UserRole, item)
            widget_item.setToolTip(item.path)
            widget_item.setSizeHint(QSize(200, 230))
            self.shelf.addItem(widget_item)
            self._load_cover(item)
        self.shelf.setVisible(bool(items))
        self.empty.setVisible(not items)
        self.empty.setText("没有匹配的视频。试试其他关键词或重新导入。" if self.library.videos
                           else "从一个文件开始播放\n\n点击「添加视频」选择文件，或把视频 / 文件夹拖到这里。\n"
                                "下载完成的视频会自动出现在这里；播放进度自动保存。")

    def _load_cover(self, video):
        """异步解码封面到 self._covers（工作线程读文件，UI 线程只收 QPixmap）。"""
        if not video.cover or video.cover in self._covers or video.cover in self._cover_pending:
            return
        generation = self._cover_generation
        self._cover_pending.add(video.cover)

        def load():
            from PySide6.QtGui import QImage, QImageReader
            if not os.path.isfile(video.cover):
                return None
            reader = QImageReader(video.cover)
            reader.setAutoTransform(True)
            # 海报区最大约 184×128 的两倍，足够清晰又不吃内存
            size = reader.size()
            if size.isValid() and size.width() > 500:
                reader.setScaledSize(size.scaled(500, 400, Qt.AspectRatioMode.KeepAspectRatio))
            image = reader.read()
            return None if image.isNull() else image

        def done(image, error):
            self._cover_pending.discard(video.cover)
            if generation != self._cover_generation:
                return
            if error or image is None:
                # 负缓存：坏封面不再反复读盘
                self._covers[video.cover] = None
                if len(self._covers) >= 240:
                    self._covers.pop(next(iter(self._covers)))
                return
            pixmap = QPixmap.fromImage(image)
            if len(self._covers) >= 240:
                self._covers.pop(next(iter(self._covers)))
            self._covers[video.cover] = pixmap
            self.shelf.viewport().update()

        self.jobs.start(load, done)

    # ------------------------------------------------------------------ 播放
    def open_item(self, item):
        if self._busy or self._closed:
            return
        video = item.data(Qt.ItemDataRole.UserRole)
        if not os.path.isfile(video.path):
            # 源文件已被删除：直接清掉这条记录，卡片跟着消失，不留死条目
            self.library.remove(video)
            self._save()
            self.refresh_shelf()
            self.status.setText(f"源文件已不存在，已清理该记录：{os.path.basename(video.path)}")
            return
        self._show_player(video)

    def _show_player(self, video):
        if self._player is not None:
            self._player.close()
        player = VideoPlayer(self.library, video, self.window())
        self._player = player
        player.changed.connect(self._player_changed)
        player.destroyed.connect(lambda *_: self._player_destroyed(player))
        player.show()
        player.raise_()
        player.activateWindow()
        self.status.setText(f"正在播放：{video.title}")

    def _player_destroyed(self, player):
        if self._player is player:
            self._player = None

    def _player_changed(self):
        if self._player is not None and self._player._closed:
            self.refresh_shelf()

    # ------------------------------------------------------------------ 维护
    def refresh_files(self):
        """「检查文件」：核对源文件，已删除的记录直接清掉，缺封面的补上。

        判定分三档，避免把「暂时看不到」当成「已经删了」：
          文件还在          → 保留；
          文件没了、目录还在 → 确实被删，移除记录（卡片随之消失）；
          连目录都访问不到   → 外置盘没插 / 网络盘断开，只提示、不动记录。
        """
        if self._busy or self._closed:
            return
        items = list(self.library.videos)
        if not items:
            self.status.setText("视频库是空的：没有需要检查的记录")
            return
        self._set_busy(True)
        self.status.setText("正在检查视频文件…")
        ffmpeg, covers_dir = self._ffmpeg, self._covers_dir

        def scan():
            results = []
            for item in items:
                if os.path.isfile(item.path) and is_video(item.path):
                    cover = ""
                    if not item.cover:
                        # 老记录补封面：先找解析附带的 _cover 图，没有再截第一帧
                        cover = (find_downloaded_cover(item.path)
                                 or extract_first_frame(item.path, ffmpeg, covers_dir))
                    results.append((item, "ok", cover))
                elif os.path.isdir(os.path.dirname(os.path.abspath(item.path))):
                    results.append((item, "gone", ""))
                else:
                    results.append((item, "offline", ""))
            return results

        def done(results, error):
            self._set_busy(False)
            if self._closed:
                return
            if error:
                self.status.setText(f"检查失败：{error}")
                return
            gone = [item for item, state, _ in results if state == "gone"]
            offline = sum(1 for _, state, _ in results if state == "offline")
            filled = 0
            for item, state, cover in results:
                if state == "ok" and cover and not item.cover:
                    item.cover = cover
                    filled += 1
            for item in gone:
                self.library.remove(item)
            saved = self._save() if (gone or filled) else True
            if gone:
                # 卡片集合变了，丢掉旧的封面解码代次，避免回填到已移除的条目
                self._cover_generation += 1
            self.refresh_shelf()
            self.status.setText(self._check_summary(len(items), gone, offline, filled, saved))

        self.jobs.start(scan, done)

    @staticmethod
    def _check_summary(total, gone, offline, filled, saved):
        """「检查文件」结果的一句话结论：先说清理了什么，再说保留了哪些。"""
        if not saved:
            return "视频记录保存失败，本次检查未落盘；请「更多 → 重新加载」后再试。"
        parts = []
        if gone:
            parts.append(f"已清理 {len(gone)} 条失效记录（源文件已不存在）")
        if offline:
            parts.append(f"{offline} 个视频所在目录暂时访问不到，记录已保留")
        if filled:
            parts.append(f"补齐 {filled} 个封面")
        if not parts:
            return f"检查完成：{total} 个视频文件都在，记录无需更新"
        return "检查完成：" + "；".join(parts)

    def _prune_missing(self):
        """启动后静默清理一次失效记录：源文件早被删掉的卡片不该继续挂着。

        只动记录、不动任何文件；所在目录整体访问不到时按「暂时不可用」保留，
        免得拔一次移动硬盘就把播放进度清空。
        """
        if self._closed or self._busy or not self.library.videos:
            return
        items = list(self.library.videos)

        def scan():
            gone, offline = [], 0
            for item in items:
                if os.path.isfile(item.path):
                    continue
                if os.path.isdir(os.path.dirname(os.path.abspath(item.path))):
                    gone.append(item)
                else:
                    offline += 1
            return gone, offline

        def done(result, error):
            if self._closed or error or not result:
                return
            gone, offline = result
            if not gone:
                if offline:
                    self.status.setText(
                        f"{offline} 个视频所在目录暂时访问不到（可能是外置盘 / 网络盘未连接），记录已保留")
                return
            for item in gone:
                self.library.remove(item)
            if not self._save():
                return
            self._cover_generation += 1
            self.refresh_shelf()
            text = f"已自动清理 {len(gone)} 条失效记录（源文件已不存在）"
            if offline:
                text += f" · {offline} 个视频所在目录暂时访问不到，记录已保留"
            self.status.setText(text)

        self.jobs.start(scan, done)

    def _delete_from_card(self, item):
        """卡片右上角 ×：走与右键「删除…」完全相同的三选一确认流程。"""
        video = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if video is None:
            return
        self._delete_source(video)

    def _context_menu(self, position):
        if self._busy:
            return
        item = self.shelf.itemAt(position)
        if item is None:
            return
        video = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        menu.addAction("播放 / 继续播放", lambda: self.open_item(item))
        menu.addAction("从头播放", lambda: (video.__setattr__("resume_ms", 0), self._save(), self.open_item(item)))
        menu.addAction("打开文件夹", lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(os.path.dirname(os.path.abspath(video.path)))))
        menu.addSeparator()
        menu.addAction("删除…", lambda: self._delete_source(video))
        menu.exec(self.shelf.mapToGlobal(position))

    def _remove(self, video):
        if self._player is not None and self._player.item is video:
            self._player.close()
        self.library.remove(video)
        self._save()
        self.refresh_shelf()

    def _delete_source(self, video):
        """右键「删除…」：让用户选择仅移除记录，还是连源文件一起删除。

        删除源文件时连带清理关联封面（解析下载的 _cover 图 + 截帧缓存）。
        文件被播放器或其它程序占用时逐个收集失败原因；主文件删不掉则保留
        记录供用户重试。
        """
        from ..core.video_library import folder_key as _fk
        path = video.path
        exists = bool(path) and os.path.isfile(path)
        if not exists:
            # 文件已经没了：等价于"仅移除记录"，直接走记录清理
            self._remove(video)
            self.status.setText(f"文件已不在原位置，已仅移除记录：{os.path.basename(path)}")
            return
        from ..core.models import fmt_size
        # 关联封面：解析下载附带的 <同名>_cover 图 + 截帧缓存（排除主文件自身，去重）
        seen, extras = set(), []
        for candidate in (find_downloaded_cover(path), video.cover):
            if (candidate and os.path.isfile(candidate)
                    and _fk(candidate) != _fk(path) and _fk(candidate) not in seen):
                seen.add(_fk(candidate))
                extras.append(candidate)
        size = fmt_size(os.path.getsize(path))
        extra_txt = f"\n同时删除 {len(extras)} 个关联封面" if extras else ""
        # 三选一：仅移除记录（源文件保留）/ 连源文件一起删 / 取消
        box = QMessageBox(self.window())
        box.setWindowTitle("删除视频")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(f"{os.path.basename(path)}"
                    f"\n大小 {size}{extra_txt}\n\n选择要执行的删除方式：")
        remove_only = box.addButton("仅移除记录", QMessageBox.ButtonRole.ActionRole)
        delete_all = box.addButton("删除记录和源文件", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(remove_only)
        box.exec()
        clicked = box.clickedButton()
        if clicked is delete_all:
            self._delete_files_and_record(video, path, extras)
        elif clicked is remove_only:
            self._remove(video)
            self.status.setText(f"已从视频库移除记录（源文件保留）：{os.path.basename(path)}")
        # 取消：什么都不做

    def _delete_files_and_record(self, video, path, extras):
        """执行源文件删除（不可撤销）。主文件删除失败时保留记录供重试。"""
        from ..core.video_library import folder_key as _fk
        # 播放器正占着这个文件时必须先关，否则 Windows 上删除会失败
        if self._player is not None and self._player.item is video:
            self._player.close()
        failed = []
        for p in [path] + extras:
            try:
                os.remove(p)
            except OSError as error:
                failed.append(f"{os.path.basename(p)}：{error}")
        if any(_fk(p) == _fk(path) for p in failed):
            # 主文件没删掉就不动记录，用户可以重试
            self.status.setText("删除失败（文件可能正被占用）：" + "；".join(failed)[:150])
            return
        # 顺带清掉封面缓存里的旧 pixmap（主文件 + 关联封面）
        self._covers.pop(video.cover, None)
        for extra in extras:
            self._covers.pop(extra, None)
        self.library.remove(video)
        self._save()
        self.refresh_shelf()
        if failed:
            self.status.setText("视频已删除，但部分封面清理失败：" + "；".join(failed)[:120])
        else:
            self.status.setText(f"已删除源文件：{os.path.basename(path)}")

    def _more_menu(self):
        menu = QMenu(self)
        menu.addAction("导入当前下载目录", lambda: self.import_paths([self.ctx.config.get("download_path")]))
        menu.addAction("重新加载已保存记录（放弃未保存修改）", self.reload_library)
        menu.addAction("使用说明", lambda: QMessageBox.information(
            self, "本地视频播放", "添加视频：选择一个或多个视频文件。\n"
            "导入文件夹：递归扫描子目录里的全部视频。\n"
            "点击卡片继续播放；鼠标移到卡片上，右上角出现 × 可直接删除，\n"
            "右键菜单也有删除（可选仅移除记录，或连源文件一起删除，删除不可撤销）。\n"
            "「检查文件」核对源文件是否还在：已被删除的记录会自动清理，\n"
            "外置盘 / 网络盘没连上时只提示、不清理。\n"
            "下载任务完成的视频会自动入库（封面图不会）。\n"
            "播放进度保存在程序目录的 video_library.json，原视频不被修改。\n"
            "个别老格式（WMV / RMVB 等）能否播放取决于系统解码器，播不了会有明确提示。"))
        menu.exec(self.moreBtn.mapToGlobal(self.moreBtn.rect().bottomLeft()))

    def reload_library(self):
        if self._busy or self._closed:
            return
        if self._player is not None:
            self._player.close()
            self._player = None
        try:
            self.library.load()
        except OSError as error:
            self.status.setText(f"重新加载失败：{error}")
            return
        self.refresh_shelf()
        self.status.setText(self.library.warning or "已重新加载磁盘上的视频记录")

    # ------------------------------------------------------------------ 拖放
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and not self._busy:
            event.acceptProposedAction()

    def dropEvent(self, event):
        if self._busy:
            return
        paths = []
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path and folder_key(path) not in {folder_key(p) for p in paths}:
                paths.append(path)
        if not paths:
            return
        event.acceptProposedAction()
        self.import_paths(paths)

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self._auto_add_queue.clear()
        self._auto_add_keys.clear()
        if self._player is not None:
            self._player.close()
        self.jobs.shutdown()
