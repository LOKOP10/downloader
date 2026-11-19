# -*- coding: utf-8 -*-
"""UI 流畅度优化：滚动、绘制、刷新节流相关的统一设置。

背景：qfluentwidgets 默认配置偏向视觉效果，在滚动和动画上开销较大。
这里提供一组轻量优化，不改变外观，只提升响应速度。
"""
import math

from PySide6.QtCore import QElapsedTimer, QEvent, QObject, QTimer, Qt
from PySide6.QtWidgets import QApplication, QAbstractScrollArea
from qfluentwidgets import ScrollArea

from . import design as D


# 用纳秒截止时间逼近 210fps；实际绘制受显示器和界面负载限制。
_SCROLL_TARGET_FPS = 210
_SCROLL_FRAME_NS = 1_000_000_000 // _SCROLL_TARGET_FPS
_SCROLL_RESPONSE_MS = 32.0


def _scroll_delegate(area):
    """Fluent 页面、表格和列表使用两种拼写，原生控件不接管。"""
    for name in ("scrollDelagate", "scrollDelegate"):
        delegate = getattr(area, name, None)
        if hasattr(delegate, "verticalSmoothScroll") and hasattr(delegate, "horizonSmoothScroll"):
            return delegate
    return None


def tune_scroll_area(area: QAbstractScrollArea, step: int = 72) -> None:
    """在实际 viewport 接管 Fluent 滚轮，避免重复定时器与逐事件重置。"""
    if area is None or _scroll_delegate(area) is None:
        return
    if isinstance(area, ScrollArea):
        area.verticalScrollBar().setSingleStep(step)
    if not getattr(area, "_perf_wheel_installed", False):
        smoother = _WheelSmoother(area)
        area.viewport().installEventFilter(smoother)
        area.installEventFilter(smoother)
        area._perf_wheel_filter = smoother
        area._perf_wheel_installed = True


class _WheelSmoother(QObject):
    """按经过的时间向滚动目标靠近，输入密集时不重置下一帧。"""

    def __init__(self, area):
        super().__init__(area)
        self._area = area
        self._bars = (area.verticalScrollBar(), area.horizontalScrollBar())
        self._position = [float(bar.value()) for bar in self._bars]
        self._target = list(self._position)
        self._setting_value = False
        self._elapsed = QElapsedTimer()
        self._last_tick_ns = 0
        self._next_frame_ns = 0
        self._running = False
        self._generation = 0
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._tick)
        for index, bar in enumerate(self._bars):
            bar.valueChanged.connect(lambda value, i=index: self._on_external_value(i, value))
            bar.rangeChanged.connect(lambda low, high, i=index: self._on_range_changed(i, low, high))
            bar.sliderPressed.connect(lambda i=index: self._reset_axis(i))

    def _on_external_value(self, index, value):
        if not self._setting_value:
            self._position[index] = self._target[index] = float(value)

    def _on_range_changed(self, index, low, high):
        self._position[index] = max(float(low), min(float(high), self._position[index]))
        self._target[index] = max(float(low), min(float(high), self._target[index]))
        if self._position == self._target:
            self._stop()

    def _stop(self):
        self._timer.stop()
        self._running = False
        self._generation += 1

    def _reset_axis(self, index):
        self._position[index] = self._target[index] = float(self._bars[index].value())
        if self._position == self._target:
            self._stop()

    def _set_value(self, index, value):
        previous = self._setting_value
        self._setting_value = True
        try:
            self._bars[index].setValue(round(value))
        finally:
            self._setting_value = previous

    def _stop_fluent(self, index):
        delegate = _scroll_delegate(self._area)
        if delegate is None:
            return
        smooth = delegate.verticalSmoothScroll if index == 0 else delegate.horizonSmoothScroll
        for name in ("fixedStepScrollEngine", "adaptiveScrollEngine"):
            engine = getattr(smooth, name, None)
            if engine is not None:
                engine.smoothMoveTimer.stop()
                engine.stepsLeftQueue.clear()

    def _queue(self, index, distance):
        bar = self._bars[index]
        old = self._target[index]
        target = max(float(bar.minimum()), min(float(bar.maximum()), old + distance))
        if target == old:
            # 正在靠近边界时先吃掉该事件，到达边界后交给外层滚动区。
            return self._running and self._position[index] != old
        self._stop_fluent(index)
        self._target[index] = target
        if D.reduce_motion():
            self._position[index] = target
            self._set_value(index, target)
            return True
        if not self._running:
            self._running = True
            self._generation += 1
            self._elapsed.start()
            self._last_tick_ns = 0
            self._next_frame_ns = _SCROLL_FRAME_NS
            self._schedule_next()
        return True

    def _pixel_scroll(self, index, delta):
        bar = self._bars[index]
        old = bar.value()
        target = max(bar.minimum(), min(bar.maximum(), old - delta))
        if target == old:
            return False
        self._stop_fluent(index)
        self._position[index] = self._target[index] = float(target)
        self._set_value(index, target)
        if self._position == self._target:
            self._stop()
        return True

    def _schedule_next(self):
        """按绝对截止时间调度，慢帧跳过过期时刻，避免密集补帧。"""
        if not self._running:
            return
        now = self._elapsed.nsecsElapsed()
        if self._next_frame_ns <= now:
            skipped = (now - self._next_frame_ns) // _SCROLL_FRAME_NS + 1
            self._next_frame_ns += skipped * _SCROLL_FRAME_NS
        remaining = self._next_frame_ns - now
        self._timer.start(max(1, (remaining + 999_999) // 1_000_000))

    def _tick(self):
        if not self._running:
            return
        generation = self._generation
        now = self._elapsed.nsecsElapsed()
        elapsed = (now - self._last_tick_ns) / 1_000_000
        self._last_tick_ns = now
        if elapsed <= 0:
            self._schedule_next()
            return
        if D.reduce_motion():
            for index, target in enumerate(self._target):
                self._position[index] = target
                self._set_value(index, target)
            self._stop()
            return
        fraction = 1.0 - math.exp(-elapsed / _SCROLL_RESPONSE_MS)
        for index in range(len(self._bars)):
            target = self._target[index]
            position = self._position[index]
            if position == target:
                continue
            position += (target - position) * fraction
            if abs(target - position) < 0.35:
                position = target
            self._position[index] = position
            self._set_value(index, position)
            if not self._running or generation != self._generation:
                return
        if self._position == self._target:
            self._stop()
        else:
            self._next_frame_ns += _SCROLL_FRAME_NS
            self._schedule_next()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Hide:
            self._stop()
            for index in range(len(self._bars)):
                self._reset_axis(index)
        if event.type() != QEvent.Type.Wheel:
            return super().eventFilter(obj, event)
        if event.modifiers() & (Qt.KeyboardModifier.ControlModifier |
                                Qt.KeyboardModifier.ShiftModifier |
                                Qt.KeyboardModifier.AltModifier |
                                Qt.KeyboardModifier.MetaModifier):
            # 修饰键事件交还原控件前，停止本次惯性，避免与其默认处理同时滚动。
            for index in range(len(self._bars)):
                self._reset_axis(index)
            return False
        pixel = event.pixelDelta()
        if pixel.x() or pixel.y():
            vertical = self._pixel_scroll(0, pixel.y()) if pixel.y() else False
            horizontal = self._pixel_scroll(1, pixel.x()) if pixel.x() else False
            handled = vertical or horizontal
        else:
            angle = event.angleDelta()
            lines = max(1, QApplication.wheelScrollLines())
            vertical = self._queue(0, -angle.y() / 120 * self._bars[0].singleStep() * lines) if angle.y() else False
            horizontal = self._queue(1, -angle.x() / 120 * self._bars[1].singleStep() * lines) if angle.x() else False
            handled = vertical or horizontal
        if handled:
            event.accept()
        return handled


def tune_scroll_areas(root) -> None:
    """优化 Fluent 页面及其表格、列表、日志；原生阅读器保持原有操作。"""
    for area in root.findChildren(QAbstractScrollArea):
        tune_scroll_area(area)


def enable_performance_hints() -> None:
    """开启若干全局绘制优化。"""
    app = QApplication.instance()
    if app is None:
        return
    try:
        app.setEffectEnabled(Qt.UI_AnimateCombo, True)
    except Exception:  # noqa: BLE001
        pass


class ProgressThrottle(QObject):
    """进度刷新节流器。

    下载任务可能以很高的频率发进度信号，但如果每个信号都直接改 UI
    （尤其是重建 QString / 重算统计），UI 线程会被压满。
    这里把同一批更新合并到固定间隔（默认 120ms）统一执行一次。
    """

    def __init__(self, interval_ms: int = 120, parent=None):
        super().__init__(parent)
        self._pending = {}
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._flush)
        self._handler = None

    def set_handler(self, fn):
        """注册真正的刷新逻辑（接收 batch: list）"""
        self._handler = fn

    def push(self, key, payload):
        """提交一次更新；同 key 会被合并，只保留最新值。"""
        self._pending[key] = payload
        if not self._timer.isActive():
            self._timer.start()

    def flush_now(self):
        if self._timer.isActive():
            self._timer.stop()
        self._flush()

    def _flush(self):
        if not self._pending:
            return
        batch = list(self._pending.values())
        self._pending.clear()
        if self._handler:
            self._handler(batch)
