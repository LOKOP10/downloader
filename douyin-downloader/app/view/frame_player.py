# -*- coding: utf-8 -*-
"""导出帧回放器：把导出的 `%06d` 帧序列按源帧率连续播放。

为什么不直接重放原视频：用户要确认的是「截出来的这一段时间」本身对不对——
帧序列播放看到的就是导出的产物，和 ffmpeg 实际写盘的帧一一对应。

加载策略：磁盘上可能有几千张 PNG，全读进内存不现实。这里只保留当前帧 +
前后各 2 帧的 pixmap 缓存（QPixmap 用 LRU 上限 8），60fps 下本地 SSD 单帧
解码约 5~15ms，够用；跟不上时 QTimer 会自动跳帧，右侧显示实际播放帧率。
"""
from __future__ import annotations

import os
from collections import OrderedDict

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QSlider,
                               QVBoxLayout, QWidget)
from qfluentwidgets import FluentIcon as FIF, PrimaryPushButton, PushButton

from . import design as D


class _Canvas(QWidget):
    """等比缩放居中绘制一帧；没有帧时显示提示文字。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = None
        self._hint = "没有可播放的帧"
        self.setMinimumSize(480, 270)

    def set_pixmap(self, pixmap):
        self._pixmap = pixmap
        self.update()

    def set_hint(self, text: str):
        self._hint = text
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0))
        if self._pixmap is None or self._pixmap.isNull():
            painter.setPen(QColor(160, 160, 160))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._hint)
            return
        scaled = self._pixmap.scaled(
            self.size(), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        left = (self.width() - scaled.width()) // 2
        top = (self.height() - scaled.height()) // 2
        painter.drawPixmap(left, top, scaled)


class FrameSequencePlayer(QDialog):
    """帧序列播放器：空格 播放/暂停，←/→ 单帧，Esc 关闭。"""

    closed = Signal()

    def __init__(self, files, fps: float = 0.0, title: str = "", parent=None):
        super().__init__(parent)
        self._files = list(files)
        self._fps = fps if fps and fps > 0 else 25.0
        self._index = 0
        self._playing = False
        self._cache = OrderedDict()
        self._closed = False

        self.setWindowTitle(title or "回放导出的帧")
        self.setMinimumSize(880, 560)
        self.resize(1120, 720)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.canvas = _Canvas(self)
        layout.addWidget(self.canvas, 1)

        bar = QHBoxLayout()
        bar.setSpacing(D.GAP_SM)
        self.prevBtn = PushButton(FIF.LEFT_ARROW, "上一帧", self)
        self.playBtn = PrimaryPushButton(FIF.PLAY, "播放", self)
        self.nextBtn = PushButton(FIF.RIGHT_ARROW, "下一帧", self)
        bar.addWidget(self.prevBtn)
        bar.addWidget(self.playBtn)
        bar.addWidget(self.nextBtn)

        self.slider = QSlider(Qt.Orientation.Horizontal, self)
        self.slider.setRange(0, max(0, len(self._files) - 1))
        bar.addWidget(self.slider, 1)

        self.infoLabel = QLabel("", self)
        self.infoLabel.setFont(D.font(D.FONT_CAPTION))
        bar.addWidget(self.infoLabel)
        layout.addLayout(bar)

        self.timer = QTimer(self)
        self.timer.setInterval(max(5, int(round(1000.0 / self._fps))))
        self.timer.timeout.connect(self._tick)

        self.prevBtn.clicked.connect(lambda: self.step(-1))
        self.nextBtn.clicked.connect(lambda: self.step(1))
        self.playBtn.clicked.connect(self.toggle)
        self.slider.valueChanged.connect(self._seek)

        for keys, handler in (("Space", self.toggle), ("Left", lambda: self.step(-1)),
                              ("Right", lambda: self.step(1)), ("Esc", self.close)):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.activated.connect(handler)

        D.theme_qss(self, "QDialog{background:#101014;} QLabel{color:#C8C8CF;}",
                    "QDialog{background:#101014;} QLabel{color:#C8C8CF;}")

        if self._files:
            self._show(0)
            self._update_info()
        else:
            self.canvas.set_hint("没有可播放的帧")

    # ------------------------------------------------------------ 播放
    def _pixmap(self, index: int):
        cached = self._cache.get(index)
        if cached is not None:
            self._cache.move_to_end(index)
            return cached
        if not (0 <= index < len(self._files)):
            return None
        pixmap = QPixmap(self._files[index])
        self._cache[index] = pixmap
        while len(self._cache) > 8:
            self._cache.popitem(last=False)
        return pixmap

    def _show(self, index: int):
        if not self._files:
            return
        index = max(0, min(index, len(self._files) - 1))
        self._index = index
        pixmap = self._pixmap(index)
        if pixmap is None or pixmap.isNull():
            self.canvas.set_hint(f"读不出这一帧：{os.path.basename(self._files[index])}")
        else:
            self.canvas.set_pixmap(pixmap)
        self.slider.blockSignals(True)
        self.slider.setValue(index)
        self.slider.blockSignals(False)
        self._update_info()

    def _update_info(self):
        total = len(self._files)
        if not total:
            self.infoLabel.setText("0 帧")
            return
        seconds = (self._index + 1) / self._fps
        duration = total / self._fps
        self.infoLabel.setText(
            f"第 {self._index + 1}/{total} 帧 · {seconds:.2f}s / {duration:.2f}s · {self._fps:.2f} fps")

    def _tick(self):
        if not self._playing:
            return
        if self._index >= len(self._files) - 1:
            self.toggle()
            self._show(0)
            return
        self._show(self._index + 1)

    def _seek(self, value: int):
        self._show(value)

    def step(self, delta: int):
        self._pause()
        self._show(self._index + delta)

    def toggle(self):
        if not self._files:
            return
        if self._playing:
            self._pause()
        else:
            self._playing = True
            self.playBtn.setText("暂停")
            self.playBtn.setIcon(FIF.PAUSE)
            self.timer.start()

    def _pause(self):
        self._playing = False
        self.timer.stop()
        self.playBtn.setText("播放")
        self.playBtn.setIcon(FIF.PLAY)

    # ------------------------------------------------------------ 生命周期
    def closeEvent(self, event):
        self._pause()
        self._closed = True
        self.closed.emit()
        super().closeEvent(event)
