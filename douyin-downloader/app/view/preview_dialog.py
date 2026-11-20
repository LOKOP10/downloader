# -*- coding: utf-8 -*-
"""解析后、下载前的独立预览窗口。视频经本机临时代理保留 CDN 所需请求头。"""

import re

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QSlider, QStyle,
                               QVBoxLayout)
from qfluentwidgets import FluentIcon as FIF, PushButton

from ..core.media_proxy import LocalMediaProxy, media_headers
from ..core.platform_registry import is_comic
from ..core.workers import JmPreviewOpenWorker, JmPreviewPageWorker
from . import design as D
from . import workspace as W

# 后台线程活到自然结束：窗口关掉时线程可能还在跑，跟着窗口一起析构会触发
# 「QThread: Destroyed while thread is still running」并把进程带崩。
_LIVE_WORKERS = set()


def _keep(worker):
    _LIVE_WORKERS.add(worker)
    worker.finished.connect(lambda: _LIVE_WORKERS.discard(worker))
    return worker


class ClickableSlider(QSlider):
    """点击轨道任意位置即跳到该处。

    原生 QSlider 在轨道上的点击只做「翻一页」（page step），既不跳到点击处，
    也不发 sliderMoved —— 于是表现出来就是「只能长按手柄拖」。这里按下时先把
    值直接定到点击位置并上报，同时保持按住后继续拖动的原生手感。
    """

    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self._scrubbing = False

    def _value_at(self, x: float) -> int:
        """把点击的横坐标换算成滑块值（按整条轨道线性映射）"""
        span = max(1, self.width())
        return QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(), int(x), span)

    def _jump_to(self, x: float) -> None:
        value = self._value_at(x)
        if value != self.value():
            self.setValue(value)
        # 值没变也要上报：外部靠这个信号去 seek 播放器
        self.sliderMoved.emit(self.value())

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or self.maximum() <= self.minimum():
            super().mousePressEvent(event)      # 时长未知时保持原生行为
            return
        self._scrubbing = True
        # 标记为「滑块被按住」：_update_seek 会跳过自动回写，避免和手动定位打架
        self.setSliderDown(True)
        self._jump_to(event.position().x())
        event.accept()

    def mouseMoveEvent(self, event):
        if not self._scrubbing:
            super().mouseMoveEvent(event)
            return
        self._jump_to(event.position().x())
        event.accept()

    def mouseReleaseEvent(self, event):
        if not self._scrubbing:
            super().mouseReleaseEvent(event)
            return
        self._jump_to(event.position().x())
        self._scrubbing = False
        self.setSliderDown(False)               # 恢复播放器的进度回写
        event.accept()


def _parse_preview_meta(label: str):
    """从画质标签提取 (短边分辨率p, fps, 是否AV1)。"""
    text = label or ""
    height = 0
    m = re.search(r"(\d{2,5})×(\d{2,5})", text)
    if m:
        height = min(int(m.group(1)), int(m.group(2)))
    else:
        mp = re.search(r"(\d{3,4})p\b", text, re.I)
        if mp:
            height = int(mp.group(1))
    mf = re.search(r"(\d{2,3})fps", text, re.I)
    fps = int(mf.group(1)) if mf else 0
    is_av1 = "av1" in text.lower()
    return height, fps, is_av1


def select_preview_option(info, selected_index: int = 0):
    """为下载前预览挑选流畅画质（>1080p 或 AV1 时优先切到 720p/1080p H.264）。

    返回 (preview_label, preview_url, downgraded_from_label)。
    未降级时第三项为空串；实际下载仍按界面所选清晰度执行。
    """
    options = getattr(info, "quality_options", None) or []
    if options and 0 <= selected_index < len(options):
        orig_label = info.quality_label(selected_index) if hasattr(info, "quality_label") else options[selected_index][0]
        orig_url = options[selected_index][1]
    else:
        orig_label = getattr(info, "quality", "") or "视频"
        orig_url = getattr(info, "play_url", "")

    if not options or not orig_url:
        return orig_label, orig_url, ""

    orig_h, _, orig_av1 = _parse_preview_meta(orig_label)
    if orig_h <= 1080 and not orig_av1:
        return orig_label, orig_url, ""

    def _score(idx: int):
        raw_lab, _ = options[idx]
        h, fps, av1 = _parse_preview_meta(raw_lab)
        if h <= 0 or h > 1080:
            return None
        # 优先 H.264 720p（带宽小且流畅），其次 1080p，再次 480p，最后其他 ≤1080p
        tier = 3 if 700 <= h <= 800 else (2 if h > 800 else (1 if h >= 480 else 0))
        return (0 if av1 else 1, tier, fps, h)

    best_idx = None
    best_key = None
    for idx in range(len(options)):
        key = _score(idx)
        if key is not None and (best_key is None or key > best_key):
            best_key = key
            best_idx = idx

    if best_idx is None or options[best_idx][1] == orig_url:
        return orig_label, orig_url, ""

    cand_label = info.quality_label(best_idx) if hasattr(info, "quality_label") else options[best_idx][0]
    return cand_label, options[best_idx][1], orig_label


class PreviewDialog(QDialog):
    """视频流预览及图集翻页；accept 表示用户在预览中确认下载。"""

    def __init__(self, info, url="", label="", cookies="", cover=None,
                 parent=None):
        super().__init__(parent)
        self.info = info
        self.cookies = cookies
        self.index = 0
        self._reply = None
        self._proxy = None
        self._player = None
        self._audio_player = None
        self._user_paused = False
        self._video_buffering = False
        self._default_status_text = "流式预览当前画质；关闭窗口即停止播放"
        self._network = QNetworkAccessManager(self)
        self._original_pix = QPixmap()
        # 禁漫预览状态：会话由后台线程建好后回填
        self._jm = None
        self._jm_cover = cover
        self._jm_chapter = 0
        self._jm_page = 0
        self._jm_pages = 0
        self._jm_chapters = 0
        self._jm_busy = False
        self.setWindowTitle("下载前预览")
        self.resize(780, 660)
        self.setObjectName("previewDialog")
        D.theme_qss(self,
                    f"QDialog#previewDialog{{background:{D.BG_PRIMARY[0]};}}",
                    f"QDialog#previewDialog{{background:{D.BG_PRIMARY[1]};}}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)
        heading = QLabel(info.title or "（无标题）", self)
        heading.setWordWrap(True)
        heading.setFont(D.font(D.FONT_TITLE, D.W_SEMI))
        # 裸 QLabel 没有 setTextColor（那是 qfluentwidgets 的扩展），用样式设色
        D.theme_qss(heading,
                    f"QLabel{{color:{D.TEXT_PRIMARY[0]};background:transparent;}}",
                    f"QLabel{{color:{D.TEXT_PRIMARY[1]};background:transparent;}}")
        layout.addWidget(heading)
        self.detail = QLabel(label or info.type_text, self)
        self.detail.setFont(D.font(D.FONT_CAPTION))
        D.theme_qss(self.detail,
                    f"QLabel{{color:{D.TEXT_TERTIARY[0]};background:transparent;}}",
                    f"QLabel{{color:{D.TEXT_TERTIARY[1]};background:transparent;}}")
        layout.addWidget(self.detail)
        self.stage = QLabel("加载中…", self)
        self.stage.setAlignment(Qt.AlignCenter)
        self.stage.setMinimumHeight(380)
        self.stage.setFont(D.font(D.FONT_CAPTION))
        # 舞台用最深的一档底 + 1px 描边：视频/图片浮在上面，边界一眼可见
        D.theme_qss(self.stage,
                    f"QLabel{{background:{D.STAGE_BG[0]};color:#A1A1AA;border:1px solid {D.STAGE_BORDER[0]};"
                    f"border-radius:{D.RADIUS_MD}px;}}",
                    f"QLabel{{background:{D.STAGE_BG[1]};color:{D.TEXT_TERTIARY[1]};border:1px solid {D.STAGE_BORDER[1]};"
                    f"border-radius:{D.RADIUS_MD}px;}}")
        layout.addWidget(self.stage, 1)
        self.controls = QHBoxLayout()
        self.controls.setSpacing(8)
        layout.addLayout(self.controls)
        self.status = QLabel("", self)
        self.status.setWordWrap(True)
        self.status.setFont(D.font(D.FONT_CAPTION))
        D.theme_qss(self.status,
                    f"QLabel{{color:{D.TEXT_TERTIARY[0]};background:transparent;}}",
                    f"QLabel{{color:{D.TEXT_TERTIARY[1]};background:transparent;}}")
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addStretch()
        close = PushButton("关闭", self)
        close.clicked.connect(self.reject)
        download = W.PrimaryButton(FIF.DOWNLOAD, "下载当前作品", self)
        download.clicked.connect(self.accept)
        actions.addWidget(close)
        actions.addWidget(download)
        layout.addLayout(actions)
        if info.is_image:
            if is_comic(info.source):
                self._setup_jm(info, cover)
            else:
                prev = PushButton("上一张", self)
                next_image = PushButton("下一张", self)
                self.counter = QLabel("", self)
                prev.clicked.connect(lambda: self._change_image(-1))
                next_image.clicked.connect(lambda: self._change_image(1))
                self.controls.addWidget(prev)
                self.controls.addStretch()
                self.controls.addWidget(self.counter)
                self.controls.addStretch()
                self.controls.addWidget(next_image)
                self._load_image()
        elif url:
            tracks = {"video": [url] + info.backups_of(url)}
            audio_url = info.audio_map.get(url, "")
            if audio_url:
                tracks["audio"] = [audio_url] + info.audio_backups_of(audio_url)
            self._proxy = LocalMediaProxy(tracks, cookies)
            self.stage.hide()
            video_widget = QVideoWidget(self)
            video_widget.setMinimumHeight(380)
            layout.insertWidget(2, video_widget, 1)
            self._player = QMediaPlayer(self)
            output = QAudioOutput(self)
            self._player.setAudioOutput(output)
            self._player.setVideoOutput(video_widget)
            self._player.setSource(QUrl(self._proxy.url("video")))
            if audio_url:
                self._audio_player = QMediaPlayer(self)
                self._audio_output = QAudioOutput(self)
                self._audio_player.setAudioOutput(self._audio_output)
                self._audio_player.setSource(QUrl(self._proxy.url("audio")))
                self._player.positionChanged.connect(self._sync_audio)
                self._player.mediaStatusChanged.connect(self._on_video_status)
                self._audio_player.errorOccurred.connect(
                    lambda *_: self.status.setText("音轨预览失败：" +
                                                   self._audio_player.errorString()))
            self._player.errorOccurred.connect(
                lambda *_: self.status.setText("视频预览失败：" + self._player.errorString()))
            play = PushButton("暂停", self)
            play.clicked.connect(lambda: self._toggle_play(play))
            self.seek = ClickableSlider(Qt.Horizontal, self)
            self.seek.setRange(0, 0)
            self._player.durationChanged.connect(self.seek.setMaximum)
            self._player.positionChanged.connect(self._update_seek)
            self.seek.sliderMoved.connect(self._seek)
            self.controls.addWidget(play)
            self.controls.addWidget(self.seek, 1)
            self.status.setText(self._default_status_text)
            self._player.play()
            if self._audio_player:
                self._audio_player.play()
        else:
            self.stage.setText("没有可预览的媒体")

    def _on_video_status(self, status):
        """视频卡住缓冲时暂停音轨等待，避免声音先跑再被强行扯回。"""
        if not self._audio_player:
            return
        if status in (QMediaPlayer.MediaStatus.BufferingMedia,
                      QMediaPlayer.MediaStatus.StalledMedia):
            self._video_buffering = True
            self._audio_player.pause()
            self.status.setText("视频缓冲中，音轨已同步等待…")
        elif status in (QMediaPlayer.MediaStatus.BufferedMedia,
                        QMediaPlayer.MediaStatus.LoadedMedia):
            if self._video_buffering:
                self._video_buffering = False
                self.status.setText(self._default_status_text)
                if not self._user_paused and self._player.isPlaying():
                    self._audio_player.setPosition(self._player.position())
                    self._audio_player.play()

    def _sync_audio(self, position):
        if (self._audio_player and not self._video_buffering
                and not self._user_paused
                and abs(self._audio_player.position() - position) > 450):
            self._audio_player.setPosition(position)

    def _toggle_play(self, button):
        if self._player.isPlaying():
            self._user_paused = True
            self._player.pause()
            if self._audio_player:
                self._audio_player.pause()
            button.setText("播放")
        else:
            self._user_paused = False
            self._player.play()
            if self._audio_player and not self._video_buffering:
                self._audio_player.setPosition(self._player.position())
                self._audio_player.play()
            button.setText("暂停")

    def _update_seek(self, position):
        if not self.seek.isSliderDown():
            self.seek.setValue(position)

    def _seek(self, position):
        self._player.setPosition(position)
        if self._audio_player:
            self._audio_player.setPosition(position)

    def _change_image(self, step):
        self.index = (self.index + step) % len(self.info.image_urls)
        self._load_image()

    def _load_image(self):
        if self._reply:
            old_reply, self._reply = self._reply, None
            old_reply.abort()
            old_reply.deleteLater()
        urls = self.info.image_urls
        self.counter.setText(f"{self.index + 1} / {len(urls)}")
        self._original_pix = QPixmap()
        self.stage.setPixmap(QPixmap())
        self.stage.setText("图片加载中…")
        self.status.setText("")
        request = QNetworkRequest(QUrl(urls[self.index]))
        for key, value in media_headers(urls[self.index], self.cookies).items():
            request.setRawHeader(key.encode(), value.encode("latin-1", errors="ignore"))
        request.setTransferTimeout(15000)
        reply = self._network.get(request)
        self._reply = reply
        reply.finished.connect(lambda: self._image_ready(reply))

    def _image_ready(self, reply):
        if reply is not self._reply:
            return
        self._reply = None
        if reply.error() != QNetworkReply.NetworkError.NoError:
            self.stage.setText("图片预览失败")
            self.status.setText(reply.errorString())
        else:
            pix = QPixmap()
            if pix.loadFromData(reply.readAll().data()):
                self._show_image(pix)
            else:
                self.stage.setText("图片格式暂不支持预览")
        reply.deleteLater()

    def _show_image(self, pix):
        self._original_pix = pix
        self.stage.setPixmap(pix.scaled(self.stage.size(), Qt.KeepAspectRatio,
                                        Qt.SmoothTransformation))

    # ------------------------------------------------------------------ 禁漫
    # 禁漫的封面之外的页都在服务器上、而且原图是切片逆序混淆过的（防爬），
    # 没法像普通图集那样把 URL 交给 QNetworkAccessManager 直接拉出来看。
    # 这里走 core.jmcomic_bridge.JmPreview：逐页取回原图 + 解码成 PNG，
    # 取页动作全部放后台线程，避免联网时界面卡住。
    def _setup_jm(self, info, cover):
        self.detail.setText("内容预览（内容页需联网逐页取回）")
        self.stage.setText("正在连接禁漫…")
        self.prevButton = PushButton("上一页", self)
        self.nextButton = PushButton("下一页", self)
        self.counter = QLabel("加载中…", self)
        self.prevButton.clicked.connect(lambda: self._jm_go(-1))
        self.nextButton.clicked.connect(lambda: self._jm_go(1))
        self.controls.addWidget(self.prevButton)
        self.controls.addStretch()
        self.controls.addWidget(self.counter)
        self.controls.addStretch()
        self.controls.addWidget(self.nextButton)
        self._jm_set_busy(True)
        self.status.setText("首次打开要拉本子目录和第一页，稍等")
        worker = _keep(JmPreviewOpenWorker(info.play_url, self.cookies))
        worker.opened.connect(self._jm_opened)
        worker.failed.connect(self._jm_failed)
        worker.start()

    def _jm_set_busy(self, busy):
        self._jm_busy = bool(busy)
        for name in ("prevButton", "nextButton"):
            button = getattr(self, name, None)
            if button is not None:
                button.setEnabled(not busy)
        if not busy:
            self._jm_refresh()

    def _jm_opened(self, session, chapters, pages, data):
        self._jm = session
        self._jm_chapters = max(1, int(chapters or 1))
        self._jm_pages = max(1, int(pages or 1))
        self._jm_chapter = 0
        self._jm_page = 0
        self.status.setText("")
        self._jm_show(data)
        self._jm_set_busy(False)

    def _jm_failed(self, message):
        self._jm_set_busy(False)
        self.status.setText(message)
        if self._jm is None and self._jm_cover is not None \
                and not self._jm_cover.isNull():
            # 内容拉不到就退回封面，至少让用户看到这本是什么
            self._show_image(self._jm_cover)
            self.detail.setText("内容读取失败，仅显示封面")

    def _jm_show(self, data) -> bool:
        pix = QPixmap()
        if not data or not pix.loadFromData(data):
            self.stage.setText("这一页解不出来")
            return False
        self._show_image(pix)
        return True

    def _jm_refresh(self):
        if self._jm is None:
            return
        if self._jm_chapters > 1:
            title = self._jm.chapter_title(self._jm_chapter)
            head = f"第 {self._jm_chapter + 1}/{self._jm_chapters} 话" + (
                f" {title}" if title else "")
            self.counter.setText(f"{head} · {self._jm_page + 1}/{self._jm_pages} 页")
        else:
            self.counter.setText(f"{self._jm_page + 1}/{self._jm_pages}")
        at_first = self._jm_chapter == 0 and self._jm_page == 0
        at_last = (self._jm_chapter >= self._jm_chapters - 1
                   and self._jm_page >= self._jm_pages - 1)
        self.prevButton.setEnabled(not self._jm_busy and not at_first)
        self.nextButton.setEnabled(not self._jm_busy and not at_last)

    def _jm_go(self, step):
        if self._jm is None or self._jm_busy:
            return
        chapter, page = self._jm_chapter, self._jm_page + step
        if page < 0:
            if chapter == 0:
                return
            chapter, page = chapter - 1, -1      # -1 = 该话最后一页，页数由后台定
        elif page >= self._jm_pages:
            if chapter >= self._jm_chapters - 1:
                return
            chapter, page = chapter + 1, 0
        self._jm_request(chapter, page)

    def _jm_request(self, chapter, page):
        self._jm_set_busy(True)
        self.status.setText("正在取页…")
        worker = _keep(JmPreviewPageWorker(self._jm, chapter, page))
        worker.loaded.connect(self._jm_loaded)
        worker.failed.connect(self._jm_failed)
        worker.start()

    def _jm_loaded(self, chapter, page, total, data):
        self._jm_chapter, self._jm_page = chapter, page
        self._jm_pages = max(1, int(total or 1))
        self.status.setText("")
        self._jm_show(data)
        self._jm_set_busy(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._original_pix.isNull():
            self.stage.setPixmap(self._original_pix.scaled(
                self.stage.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def done(self, result):
        if self._reply:
            old_reply, self._reply = self._reply, None
            old_reply.abort()
            old_reply.deleteLater()
        if self._player:
            self._player.stop()
            self._player.setSource(QUrl())
        if self._audio_player:
            self._audio_player.stop()
            self._audio_player.setSource(QUrl())
        if self._proxy:
            self._proxy.stop()
            self._proxy = None
        super().done(result)

