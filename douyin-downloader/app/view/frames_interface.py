# -*- coding: utf-8 -*-
"""导出原始帧页面：像剪辑软件那样在时间轴上拖两端夹住区间，逐帧导出并可回放。

左半是预览播放器 + 双滑块时间轴（`range_slider.py`）：拖 in/out 手柄时画面
实时吸附到该端点，便于对帧；点轨道空白处只移动播放头预览，不动区间。

右半是输出设置；底部是导出与回放。工作线程只持有不可变任务快照 + 取消标记 +
信号桥，不碰任何 QWidget（与 MP4 转 GIF 页同一套约定）。
"""
from __future__ import annotations

import os
import threading

from PySide6.QtCore import QObject, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PySide6.QtWidgets import (QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QProgressBar, QSizePolicy, QVBoxLayout, QWidget)
from qfluentwidgets import (ComboBox, FluentIcon as FIF, PrimaryPushButton,
                            PushButton, ScrollArea)

from ..core.ffmpeg import ffmpeg_path
from ..core.frame_export import (FrameExportCancelled, FrameExportError,
                                 export_frames, format_time, frame_files,
                                 probe_video)
from ..core.system_open import open_local_path
from . import design as D
from . import workspace as W
from .frame_player import FrameSequencePlayer
from .range_slider import RangeSlider

_VIDEO_SUFFIXES = (".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".wmv", ".m4v", ".ts")


def pick_start_dir(video: str, download_path: str) -> str:
    """「选择视频」对话框的起始目录。

    优先当前已选视频所在目录（连续处理同一批素材），其次用设置里的下载目录
    （视频都下到那里，省得每次手动翻），最后才退回用户主目录。
    """
    if video and os.path.isfile(video):
        return os.path.dirname(video)
    if download_path and os.path.isdir(download_path):
        return download_path
    return os.path.expanduser("~")


class _Signals(QObject):
    progress = Signal(int, int, str)
    finished = Signal(bool, str)


class _ExportJob:
    """导出线程：只在构造时接收纯数据，运行期通过信号回主线程。"""

    def __init__(self, source, out_dir, start, end, options):
        self.signals = _Signals()
        self.cancel = threading.Event()
        self.source = source
        self.out_dir = out_dir
        self.start = start
        self.end = end
        self.options = options
        self.thread = threading.Thread(target=self.run, name="frame-export", daemon=True)

    def run(self):
        try:
            info = export_frames(
                self.source, self.out_dir, self.start, self.end,
                self.options.get("format", "png"),
                self.options.get("level", 1),
                self.cancel, self.signals.progress.emit,
                self.options.get("ffmpeg", ""))
            detail = f"{info['frames']} 帧 · 用时 {info['elapsed']:.1f}s"
            self.signals.finished.emit(True, detail)
        except FrameExportCancelled:
            self.signals.finished.emit(False, "已取消，已导出的帧保留在输出目录")
        except FrameExportError as error:
            self.signals.finished.emit(False, str(error))
        except Exception as error:  # noqa: BLE001 - 兜住任何意外，别让线程静默死掉
            self.signals.finished.emit(False, f"导出失败：{error}")


class _PreviewCanvas(QLabel):
    """视频画面：等比缩放居中，黑底。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setText("选一个视频，这里就是预览画面")
        self.setMinimumSize(520, 292)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._image = None
        self.setStyleSheet("background:#0B0B0F;color:#71717A;border-radius:8px;")

    def set_image(self, image: QImage):
        if image is None or image.isNull():
            return
        self._image = image
        self._refresh()

    def clear(self, text: str = "选一个视频，这里就是预览画面"):
        # 注意：QLabel 自己有 clear()，覆盖它以后必须用 super() 调，
        # 否则这里会无限递归（实测直接段错误 SIGSEGV，exit=139）。
        self._image = None
        super().clear()
        self.setText(text)

    def _refresh(self):
        if self._image is None:
            return
        pixmap = QPixmap.fromImage(self._image).scaled(
            self.size(), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self.setPixmap(pixmap)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh()


class FramesInterface(ScrollArea):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("framesInterface")
        self.setAcceptDrops(True)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._busy = False
        self._closed = False
        self._job = None
        self._video = ""
        self._info = {}
        self._duration_ms = 0
        self._fps = 0.0
        self._previewing = False
        self._last_output = ""
        self._player = None

        self.content = QWidget(self)
        self.content.setObjectName("framesContent")
        self.setWidget(self.content)
        D.theme_qss(self.content, "QWidget#framesContent{background:transparent;}",
                    "QWidget#framesContent{background:transparent;}")

        layout = W.page_body(self.content, pad=24, spacing=12)
        layout.addWidget(W.PageHeading(
            "导出原始帧",
            "在时间轴上拖动两端手柄夹住一段，把这段的每一帧原样导出（不抽帧、不补帧），导出后可回放确认。",
            self.content))

        columns = QHBoxLayout()
        columns.setSpacing(12)

        # ---------------- 左：预览 + 时间轴 ----------------
        left = W.Panel(self.content, padding=(16, 12, 16, 12), spacing=8)
        head = QHBoxLayout()
        head.setSpacing(D.GAP_SM)
        head.addWidget(W.PanelTitle("预览与截取", left, note="拖两端手柄夹取"))
        head.addStretch(1)
        self.pickBtn = PrimaryPushButton(FIF.ADD, "选择视频", left)
        self.pickBtn.clicked.connect(self._choose_video)
        self.clearBtn = PushButton(FIF.CLOSE, "清空", left)
        self.clearBtn.clicked.connect(self._clear_video)
        head.addWidget(self.pickBtn)
        head.addWidget(self.clearBtn)
        left.body.addLayout(head)

        self.canvas = _PreviewCanvas(left)
        left.body.addWidget(self.canvas, 1)

        controls = QHBoxLayout()
        controls.setSpacing(D.GAP_SM)
        self.playBtn = PushButton(FIF.PLAY, "播放所选", left)
        self.playBtn.setToolTip("从起点播到终点；到终点自动停下并回到起点")
        self.playBtn.clicked.connect(self.toggle_preview)
        self.toInBtn = PushButton(FIF.LEFT_ARROW, "到起点", left)
        self.toInBtn.clicked.connect(lambda: self._seek_to(self.slider.in_ms() if hasattr(self, "slider") else 0))
        self.toOutBtn = PushButton(FIF.RIGHT_ARROW, "到终点", left)
        self.toOutBtn.clicked.connect(lambda: self._seek_to(self.slider.out_ms() if hasattr(self, "slider") else 0))
        self.zoomBtn = PushButton(FIF.ZOOM, "放大选区", left)
        self.zoomBtn.setToolTip("把刻度收窄到选区附近，短区间也能拖准；再点一次回到全片")
        self.zoomBtn.clicked.connect(self._toggle_zoom)
        for button in (self.playBtn, self.toInBtn, self.toOutBtn, self.zoomBtn):
            button.setEnabled(False)
            controls.addWidget(button)
        controls.addStretch(1)
        left.body.addLayout(controls)

        self.slider = RangeSlider(left)
        self.slider.setEnabled(False)
        self.slider.inChanged.connect(self._on_in_changed)
        self.slider.outChanged.connect(self._on_out_changed)
        self.slider.seekRequested.connect(self._seek_to)
        left.body.addWidget(self.slider)

        self.rangeLabel = W.text_label("还没有选择视频", left, D.TEXT_SECONDARY,
                                       D.FONT_CAPTION, wrap=True)
        left.body.addWidget(self.rangeLabel)
        self.fileLabel = W.text_label("", left, D.TEXT_TERTIARY, D.FONT_CAPTION, wrap=True)
        left.body.addWidget(self.fileLabel)
        columns.addWidget(left, 3)

        # ---------------- 右：输出设置 ----------------
        right = W.Panel(self.content, padding=(16, 12, 16, 12), spacing=10)
        self.editPanel = right
        right.body.addWidget(W.PanelTitle("输出设置", right))
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(D.GAP_SM)
        self.formatBox = ComboBox(right)
        for name, label in (("png", "PNG（无损，推荐）"), ("bmp", "BMP（不压缩，体积最大）"),
                            ("tif", "TIFF（无损）")):
            self.formatBox.addItem(label, userData=name)
        self.formatBox.currentIndexChanged.connect(self._sync_level_state)
        form.addRow("输出格式", self.formatBox)
        self.levelBox = ComboBox(right)
        for level in range(1, 10):
            self.levelBox.addItem(f"{level}" + ("（最快）" if level == 1 else ""), userData=level)
        form.addRow("PNG 压缩", self.levelBox)
        right.body.addLayout(form)
        right.body.addWidget(W.text_label(
            "输出目录固定为视频旁边的「<文件名>_frames」，与拖拽 bat 的老口径一致；"
            "第二张图之后导出的帧按 000001.png 起顺序编号。",
            right, D.TEXT_TERTIARY, D.FONT_CAPTION, wrap=True))
        right.body.addStretch(1)
        columns.addWidget(right, 2)
        layout.addLayout(columns)

        # ---------------- 底部：执行与结果 ----------------
        result = W.Panel(self.content, padding=(16, 12, 16, 12), spacing=8)
        buttons = QHBoxLayout()
        buttons.setSpacing(D.GAP_SM)
        self.startBtn = PrimaryPushButton(FIF.PLAY, "开始导出", result)
        self.startBtn.clicked.connect(self.start_export)
        self.cancelBtn = PushButton(FIF.CLOSE, "取消", result)
        self.cancelBtn.clicked.connect(self.cancel_export)
        self.cancelBtn.setEnabled(False)
        self.resultPlayBtn = PushButton(FIF.VIEW, "回放导出的帧", result)
        self.resultPlayBtn.setToolTip("把导出的帧按源帧率连续播放")
        self.resultPlayBtn.clicked.connect(self.play_result)
        self.resultPlayBtn.setEnabled(False)
        self.openBtn = PushButton(FIF.FOLDER, "打开输出目录", result)
        self.openBtn.clicked.connect(self.open_output)
        self.openBtn.setEnabled(False)
        for button in (self.startBtn, self.cancelBtn, self.resultPlayBtn, self.openBtn):
            buttons.addWidget(button)
        buttons.addStretch(1)
        result.body.addLayout(buttons)

        self.progressBar = QProgressBar(result)
        self.progressBar.setRange(0, 1)
        self.progressBar.setValue(0)
        result.body.addWidget(self.progressBar)
        self.statusLabel = W.text_label("就绪：先选视频，再拖时间轴两端", result,
                                        D.TEXT_TERTIARY, D.FONT_CAPTION, wrap=True)
        result.body.addWidget(self.statusLabel)
        layout.addWidget(result)
        layout.addStretch(1)

        # ---------------- 播放器 ----------------
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.7)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        self.sink = QVideoSink(self)
        self.player.setVideoSink(self.sink)
        self.sink.videoFrameChanged.connect(self._on_frame)
        self.player.durationChanged.connect(self._on_duration)
        self.player.positionChanged.connect(self._on_position)
        self.player.playbackStateChanged.connect(self._on_state)

        self._sync_level_state()

    # ------------------------------------------------------------ 载入
    def _choose_video(self):
        start_dir = pick_start_dir(self._video, self.ctx.config.get("download_path") or "")
        path, _ = QFileDialog.getOpenFileName(
            self, "选择视频", start_dir,
            "视频文件 (*.mp4 *.mov *.mkv *.avi *.webm *.flv *.wmv *.m4v *.ts);;所有文件 (*)")
        if path:
            self.set_video(path)

    def set_video(self, path: str):
        if self._busy or not path or not os.path.isfile(path):
            return
        self._video = path
        self.fileLabel.setText(os.path.basename(path))
        self.fileLabel.setToolTip(path)
        self.resultPlayBtn.setEnabled(False)
        try:
            self._info = probe_video(path, ffmpeg_path(self.ctx.config.base_dir) or "")
        except FrameExportError as error:
            self._info = {}
            self.statusLabel.setText(str(error))
        info = self._info
        self._fps = info.get("fps") or 0.0
        if self._fps > 0:
            self.slider.set_step_ms(int(round(1000.0 / self._fps)))
        # 播放器读到的时长才是时间轴的真实刻度
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.pause()
        self.canvas.clear("正在读取视频…")
        for button in (self.playBtn, self.toInBtn, self.toOutBtn, self.zoomBtn):
            button.setEnabled(True)
        self.slider.setEnabled(True)
        self._refresh_range_label()

    def _on_duration(self, duration: int):
        self._duration_ms = max(0, int(duration))
        if not self._duration_ms:
            return
        # 默认整段：两个手柄分居轨道两端，先粗拖再放大微调（与剪辑软件一致）
        self.slider.set_duration(self._duration_ms)
        self.player.setPosition(0)
        self._refresh_range_label()

    def _on_frame(self, frame):
        image = frame.toImage()
        if image is not None and not image.isNull():
            self.canvas.set_image(image)

    def _clear_video(self):
        if self._busy:
            return
        self._video = ""
        self._info = {}
        self._duration_ms = 0
        self._fps = 0.0
        self.player.stop()
        self.player.setSource(QUrl())
        self.canvas.clear()
        self.slider.setEnabled(False)
        for button in (self.playBtn, self.toInBtn, self.toOutBtn, self.zoomBtn):
            button.setEnabled(False)
        self.resultPlayBtn.setEnabled(False)
        self.fileLabel.setText("")
        self.rangeLabel.setText("还没有选择视频")
        self.statusLabel.setText("就绪：先选视频，再拖时间轴两端")

    def _sync_level_state(self):
        fmt = self.formatBox.currentData() or "png"
        self.levelBox.setEnabled(fmt in ("png", "tif"))

    # ------------------------------------------------------------ 区间
    def _refresh_range_label(self):
        in_ms, out_ms = self.slider.in_ms(), self.slider.out_ms()
        span_ms = max(0, out_ms - in_ms)
        frames = int(round(span_ms / 1000.0 * self._fps)) if self._fps else 0
        parts = [f"起 {format_time(in_ms / 1000.0)}", f"止 {format_time(out_ms / 1000.0)}",
                 f"区间 {span_ms / 1000.0:.3f}s"]
        if frames:
            parts.append(f"约 {frames} 帧")
        if self._fps:
            parts.append(f"{self._fps:.2f} fps")
        if self.slider.is_zoomed():
            view_in, view_out = self.slider.view_range()
            parts.append(f"刻度 {format_time(view_in / 1000.0)}–{format_time(view_out / 1000.0)}")
        self.rangeLabel.setText(" · ".join(parts))

    def _on_in_changed(self, ms: int):
        self._refresh_range_label()
        self._pause_preview()
        self._seek_to(ms)

    def _on_out_changed(self, ms: int):
        self._refresh_range_label()
        self._pause_preview()
        self._seek_to(ms)

    def _toggle_zoom(self):
        if self.slider.duration_ms() <= 0:
            return
        if self.slider.is_zoomed():
            self.slider.reset_view()
            self.zoomBtn.setText("放大选区")
        else:
            self.slider.zoom_to_selection()
            self.zoomBtn.setText("回到全片")
        self._refresh_range_label()

    def _seek_to(self, ms: int):
        if not self._video:
            return
        self.player.setPosition(max(0, min(int(ms), self._duration_ms or int(ms))))
        self.slider.set_position(ms)

    def toggle_preview(self):
        if not self._video:
            return
        if self._previewing:
            self._pause_preview()
            return
        self._previewing = True
        self.playBtn.setText("暂停")
        self.playBtn.setIcon(FIF.PAUSE)
        self.player.setPosition(self.slider.in_ms())
        self.player.play()

    def _pause_preview(self):
        if self._previewing:
            self.player.pause()
        self._previewing = False
        self.playBtn.setText("播放所选")
        self.playBtn.setIcon(FIF.PLAY)

    def _on_position(self, position: int):
        if self._closed:
            return
        self.slider.set_position(position)
        if self._previewing and position >= self.slider.out_ms() - 1:
            self._pause_preview()
            self.player.setPosition(self.slider.in_ms())
            self.slider.set_position(self.slider.in_ms())

    def _on_state(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        if not playing and self._previewing:
            self._previewing = False
            self.playBtn.setText("播放所选")
            self.playBtn.setIcon(FIF.PLAY)

    # ------------------------------------------------------------ 导出
    def start_export(self):
        if self._busy or self._closed:
            return
        if not self._video:
            self.statusLabel.setText("请先选择视频")
            return
        in_ms, out_ms = self.slider.in_ms(), self.slider.out_ms()
        if out_ms - in_ms < 2:
            self.statusLabel.setText("区间太短：在时间轴上把两端拉开一点")
            return
        ffmpeg = ffmpeg_path(self.ctx.config.base_dir)
        if not ffmpeg:
            self.statusLabel.setText("未找到 FFmpeg，请前往设置下载 FFmpeg 组件后重试。")
            return
        self._pause_preview()
        out_dir = os.path.splitext(self._video)[0] + "_frames"
        options = {
            "format": self.formatBox.currentData() or "png",
            "level": self.levelBox.currentData() or 1,
            "ffmpeg": ffmpeg,
        }
        job = _ExportJob(self._video, out_dir, in_ms / 1000.0, out_ms / 1000.0, options)
        self._job = job
        self._busy = True
        self.editPanel.setEnabled(False)
        self.pickBtn.setEnabled(False)
        self.clearBtn.setEnabled(False)
        self.slider.setEnabled(False)
        for button in (self.playBtn, self.toInBtn, self.toOutBtn, self.zoomBtn):
            button.setEnabled(False)
        self.startBtn.setEnabled(False)
        self.cancelBtn.setEnabled(True)
        self.resultPlayBtn.setEnabled(False)
        self.progressBar.setRange(0, 0)
        self.statusLabel.setText("正在导出…")
        job.signals.progress.connect(self._on_progress, Qt.ConnectionType.QueuedConnection)
        job.signals.finished.connect(self._on_finished, Qt.ConnectionType.QueuedConnection)
        self.destroyed.connect(job.cancel.set)
        job.thread.start()

    @Slot(int, int, str)
    def _on_progress(self, done, total, message):
        if self._closed:
            return
        self.statusLabel.setText(message)
        self.progressBar.setRange(0, max(0, total))
        self.progressBar.setValue(max(0, min(done, total or done)))

    @Slot(bool, str)
    def _on_finished(self, ok, detail):
        self._busy = False
        self.cancelBtn.setEnabled(False)
        self.pickBtn.setEnabled(True)
        self.clearBtn.setEnabled(True)
        has_video = bool(self._video)
        self.slider.setEnabled(has_video)
        for button in (self.playBtn, self.toInBtn, self.toOutBtn, self.zoomBtn):
            button.setEnabled(has_video)
        self.startBtn.setEnabled(True)
        self.editPanel.setEnabled(True)
        self.progressBar.setRange(0, 1)
        self.progressBar.setValue(1)
        if self._closed:
            return
        out_dir = self._job.out_dir if self._job else ""
        if ok:
            self._last_output = out_dir
            self.resultPlayBtn.setEnabled(bool(frame_files(out_dir, self._format())))
            self.openBtn.setEnabled(True)
            self.statusLabel.setText(f"完成：{detail} → {out_dir}")
        else:
            self.statusLabel.setText(detail)
            if os.path.isdir(out_dir) and frame_files(out_dir, self._format()):
                self._last_output = out_dir
                self.resultPlayBtn.setEnabled(True)
                self.openBtn.setEnabled(True)
        if self._job:
            try:
                self.destroyed.disconnect(self._job.cancel.set)
            except (RuntimeError, TypeError):
                pass

    def _format(self):
        return self.formatBox.currentData() or "png"

    def cancel_export(self):
        if self._busy and self._job:
            self._job.cancel.set()
            self.cancelBtn.setEnabled(False)
            self.statusLabel.setText("正在取消，等待当前帧写完…")

    # ------------------------------------------------------------ 回放
    def play_result(self):
        if not self._last_output:
            return
        files = frame_files(self._last_output, self._format())
        if not files:
            self.statusLabel.setText("输出目录里没有帧可回放")
            return
        if self._player is not None:
            try:
                self._player.close()
            except RuntimeError:
                pass
        self._player = FrameSequencePlayer(
            files, self._fps, title=f"{os.path.basename(self._video)} · {len(files)} 帧",
            parent=self)
        self._player.show()
        self._player.raise_()

    def open_output(self):
        if self._last_output:
            open_local_path(self._last_output)

    # ------------------------------------------------------------ 拖放
    def dragEnterEvent(self, event):
        if self._busy or self._closed or not event.mimeData().hasUrls():
            return
        for url in event.mimeData().urls():
            if url.isLocalFile() and url.toLocalFile().lower().endswith(_VIDEO_SUFFIXES):
                event.acceptProposedAction()
                return

    def dropEvent(self, event):
        if self._busy or self._closed:
            return
        for url in event.mimeData().urls():
            if url.isLocalFile() and url.toLocalFile().lower().endswith(_VIDEO_SUFFIXES):
                self.set_video(url.toLocalFile())
                event.acceptProposedAction()
                return

    def shutdown(self):
        self._closed = True
        self._pause_preview()
        try:
            self.player.stop()
        except RuntimeError:
            pass
        if self._job:
            self._job.cancel.set()
        if self._player is not None:
            try:
                self._player.close()
            except RuntimeError:
                pass
            self._player = None
