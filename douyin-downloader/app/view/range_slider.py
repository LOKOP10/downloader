# -*- coding: utf-8 -*-
"""双滑块时间轴：剪辑软件那种「两端夹住」的区间选择。

一条轨道上有两个手柄（in / out）和一个播放头：

    in 手柄                播放头          out 手柄
      ▼                     │                 ▼
   ───●════════════════════─┼─────────────────●───
      └────── 选中的区间（强调色）──────────┘

交互与剪辑软件一致：
- 拖 in / out 手柄 → 改区间，同时把播放头吸附到该端点（画面跟着走，便于对帧）
- 点轨道空白处 → 只移动播放头预览，不改区间
- 键盘 ←/→ → 以 1 帧为步长微调「最近一次拖过的手柄」

**视图缩放**：227 秒的视频里选 2 秒，两个手柄在全片刻度上会挤成一个点，
根本没法拖。`zoom_to_selection()` 把刻度窗口收窄到选区的 2 倍（前后各留
50%），`reset_view()` 回到全片；坐标换算始终走视图窗口，所以放大后
拖动的仍是同一对 ms 值。

坐标换算抽成模块级纯函数（`ms_at` / `x_at`，参数是"窗口起点 + 窗口长度"），
方便单测；控件本身不依赖播放器。
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

from . import design as D
from . import workspace as W

PADDING = 14          # 轨道两端留白
HANDLE_R = 7          # 手柄半径
HIT_SLOP = 10         # 命中手柄的容差（像素）
TRACK_H = 4           # 轨道高度


def ms_at(x: float, width: int, span_ms: int) -> int:
    """像素 x → 相对窗口起点的毫秒偏移（超出两端归到 0 / span_ms）"""
    span = max(1, width - PADDING * 2)
    ratio = (float(x) - PADDING) / span
    ratio = min(1.0, max(0.0, ratio))
    return int(round(ratio * max(0, span_ms)))


def x_at(offset_ms: int, width: int, span_ms: int) -> float:
    """相对窗口起点的毫秒偏移 → 像素 x"""
    span = max(1, width - PADDING * 2)
    if span_ms <= 0:
        return float(PADDING)
    ratio = min(1.0, max(0.0, float(offset_ms) / float(span_ms)))
    return PADDING + ratio * span


class RangeSlider(QWidget):
    """区间滑块：in/out 两端可拖，播放头可点，刻度窗口可缩放。"""

    inChanged = Signal(int)        # 毫秒
    outChanged = Signal(int)
    seekRequested = Signal(int)
    dragFinished = Signal()
    viewChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration = 0
        self._in = 0
        self._out = 0
        self._position = 0
        self._view_in = 0
        self._view_out = 0
        self._dragging = None      # "in" / "out" / "head"
        self._active = "out"
        self._step_ms = 33
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(44)
        self.setMouseTracking(True)

    # ------------------------------------------------------------ 数据
    def duration_ms(self) -> int:
        return self._duration

    def in_ms(self) -> int:
        return self._in

    def out_ms(self) -> int:
        return self._out

    def position_ms(self) -> int:
        return self._position

    def view_range(self):
        return self._view_in, self._view_out

    def is_zoomed(self) -> bool:
        return self._view_in > 0 or (self._view_out < self._duration and self._view_out > 0)

    def set_step_ms(self, step: int):
        self._step_ms = max(1, int(step))

    def set_duration(self, ms: int):
        self._duration = max(0, int(ms))
        self._in = 0
        self._out = self._duration
        self._position = 0
        self.reset_view()
        self.update()

    def set_range(self, in_ms: int, out_ms: int, emit: bool = True):
        in_ms = max(0, min(int(in_ms), self._duration or int(in_ms)))
        out_ms = max(0, min(int(out_ms), self._duration or int(out_ms)))
        if out_ms < in_ms:
            in_ms, out_ms = out_ms, in_ms
        self._in, self._out = in_ms, out_ms
        self.update()
        if emit:
            self.inChanged.emit(self._in)
            self.outChanged.emit(self._out)

    def set_position(self, ms: int):
        self._position = max(0, min(int(ms), self._duration or int(ms)))
        self.update()

    # ------------------------------------------------------------ 视图
    def reset_view(self):
        self._view_in = 0
        self._view_out = self._duration
        self.viewChanged.emit()
        self.update()

    def zoom_to_selection(self, pad_ratio: float = 0.5):
        """把刻度窗口收窄到选区两侧各留 pad_ratio 倍选区长度的范围"""
        if self._duration <= 0:
            return
        span = max(1, self._out - self._in)
        pad = int(span * pad_ratio)
        start = max(0, self._in - pad)
        end = min(self._duration, self._out + pad)
        if end - start < max(4, self._step_ms * 4):
            return
        self._view_in, self._view_out = start, end
        self.viewChanged.emit()
        self.update()

    # ------------------------------------------------------------ 换算
    def _span(self) -> int:
        return max(1, self._view_out - self._view_in)

    def _ms_at(self, x: float) -> int:
        return self._view_in + ms_at(x, self.width(), self._span())

    def _x_at(self, ms: int) -> float:
        return x_at(int(ms) - self._view_in, self.width(), self._span())

    # ------------------------------------------------------------ 绘制
    def sizeHint(self) -> QSize:
        return QSize(520, 44)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        width = self.width()
        mid = self.height() / 2.0
        track_y = mid - TRACK_H / 2.0
        span = max(1, width - PADDING * 2)

        # 轨道
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(W.col(D.SURFACE_HI))
        painter.drawRoundedRect(PADDING, track_y, span, TRACK_H, 2, 2)

        # 选中区间（超出视图时钳到边缘）
        x_in = self._x_at(self._in)
        x_out = self._x_at(self._out)
        painter.setBrush(W.accent())
        painter.drawRoundedRect(x_in, track_y, max(2.0, x_out - x_in), TRACK_H, 2, 2)

        # 播放头
        x_pos = self._x_at(self._position)
        pen = painter.pen()
        pen.setColor(W.col(D.TEXT_TERTIARY))
        pen.setWidthF(1.0)
        painter.setPen(pen)
        painter.drawLine(int(x_pos), int(track_y - 9), int(x_pos), int(track_y + TRACK_H + 9))

        # 两个手柄
        for name, x in (("in", x_in), ("out", x_out)):
            path = QPainterPath()
            path.addEllipse(x - HANDLE_R, mid - HANDLE_R, HANDLE_R * 2, HANDLE_R * 2)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(W.accent() if (self._dragging == name or self._active == name)
                             else W.col(D.BORDER_STRONG))
            painter.drawPath(path)
            inner = QPainterPath()
            inner.addEllipse(x - 2.5, mid - 2.5, 5, 5)
            painter.setBrush(W.col(D.SURFACE))
            painter.drawPath(inner)

    # ------------------------------------------------------------ 鼠标
    def _nearest(self, x: float):
        d_in = abs(x - self._x_at(self._in))
        d_out = abs(x - self._x_at(self._out))
        if min(d_in, d_out) > HIT_SLOP:
            return None
        return "in" if d_in <= d_out else "out"

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self._duration <= 0:
            return
        x = event.position().x()
        target = self._nearest(x)
        if target is None:
            self._dragging = "head"
            self._position = self._ms_at(x)
            self.seekRequested.emit(self._position)
            self.update()
            return
        self._dragging = target
        self._active = target
        self._apply_drag(x)
        self.update()

    def mouseMoveEvent(self, event):
        if self._dragging is None:
            return
        self._apply_drag(event.position().x())

    def mouseReleaseEvent(self, event):
        if self._dragging is None:
            return
        finished = self._dragging != "head"
        self._dragging = None
        self.update()
        if finished:
            self.dragFinished.emit()

    def _apply_drag(self, x: float):
        value = max(0, min(self._ms_at(x), self._duration))
        if self._dragging == "in":
            value = min(value, max(0, self._out - 1))
            if value == self._in:
                return
            self._in = value
            self._position = value
            self.inChanged.emit(value)
        elif self._dragging == "out":
            value = max(value, min(self._duration, self._in + 1))
            if value == self._out:
                return
            self._out = value
            self._position = value
            self.outChanged.emit(value)
        else:
            self._position = value
            self.seekRequested.emit(value)
        self.update()

    # ------------------------------------------------------------ 键盘
    def keyPressEvent(self, event):
        if self._duration <= 0:
            return super().keyPressEvent(event)
        key = event.key()
        if key not in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            return super().keyPressEvent(event)
        delta = -self._step_ms if key == Qt.Key.Key_Left else self._step_ms
        if self._active == "in":
            self.set_range(self._in + delta, self._out)
            self.seekRequested.emit(self._in)
        else:
            self.set_range(self._in, self._out + delta)
            self.seekRequested.emit(self._out)
        event.accept()
