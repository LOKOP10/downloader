"""210 帧滚动目标与页面动效的真实事件回归。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import time
import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QPoint, QPointF, QTimer, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QListWidget, QWidget
from qfluentwidgets import ListWidget, PlainTextEdit, ScrollArea, TableWidget

from app.core.config import Config
from app.main_window import MainWindow
from app.view import design as D
from app.view.perf import _SCROLL_TARGET_FPS, tune_scroll_area, tune_scroll_areas


def wait_ms(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wheel(area, angle_x=0, angle_y=0, pixel_x=0, pixel_y=0, modifiers=Qt.KeyboardModifier.NoModifier):
    viewport = area.viewport()
    local = QPointF(viewport.rect().center())
    global_pos = QPointF(viewport.mapToGlobal(viewport.rect().center()))
    return QWheelEvent(local, global_pos, QPoint(pixel_x, pixel_y), QPoint(angle_x, angle_y),
                       Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.ScrollUpdate, False)


class SmoothWheelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.previous_motion = D.reduce_motion()
        D.set_reduce_motion(False)
        self.area = ScrollArea()
        self.area.resize(480, 320)
        self.content = QWidget()
        self.content.setFixedSize(2000, 5000)
        self.area.setWidget(self.content)
        tune_scroll_area(self.area)
        self.area.show()
        self.app.processEvents()
        self.vbar = self.area.verticalScrollBar()
        self.hbar = self.area.horizontalScrollBar()
        self.vbar.setValue(500)
        self.hbar.setValue(100)
        self.smoother = self.area._perf_wheel_filter

    def tearDown(self):
        self.area.close()
        self.area.deleteLater()
        self.app.processEvents()
        D.set_reduce_motion(self.previous_motion)

    def send(self, **kwargs):
        event = wheel(self.area, **kwargs)
        QApplication.sendEvent(self.area.viewport(), event)
        return event

    def test_viewport_wheel_uses_precise_timer_and_stops_at_expected_position(self):
        self.assertEqual(_SCROLL_TARGET_FPS, 210)
        self.assertEqual(self.smoother._timer.timerType(), Qt.TimerType.PreciseTimer)
        self.assertTrue(self.smoother._timer.isSingleShot())
        expected = 500 + self.vbar.singleStep() * QApplication.wheelScrollLines()
        self.send(angle_y=-120)
        self.assertTrue(self.smoother._timer.isActive())
        self.assertGreaterEqual(self.smoother._timer.interval(), 1)
        self.assertLessEqual(self.smoother._timer.interval(), 5)
        wait_ms(450)
        self.assertEqual(self.vbar.value(), expected)
        self.assertFalse(self.smoother._timer.isActive())
        delegate = self.area.scrollDelagate.verticalSmoothScroll
        self.assertFalse(delegate.fixedStepScrollEngine.smoothMoveTimer.isActive())
        self.assertFalse(delegate.adaptiveScrollEngine.smoothMoveTimer.isActive())
        self.send(angle_y=120)
        wait_ms(450)
        self.assertEqual(self.vbar.value(), 500)

    def test_continuous_fast_input_keeps_advancing_and_preserves_total_distance(self):
        count = [0]
        observed_while_sending = []
        sender = QTimer()
        sender.setTimerType(Qt.TimerType.PreciseTimer)
        sender.setInterval(4)

        def send_one():
            self.send(angle_y=-15)
            count[0] += 1
            observed_while_sending.append(self.vbar.value())
            if count[0] == 20:
                sender.stop()

        sender.timeout.connect(send_one)
        sender.start()
        wait_ms(550)
        self.assertEqual(count[0], 20)
        self.assertGreater(max(observed_while_sending[:-1]), 500)
        distance = round(20 * 15 / 120 * self.vbar.singleStep() * QApplication.wheelScrollLines())
        self.assertEqual(self.vbar.value(), 500 + distance)
        self.assertFalse(self.smoother._timer.isActive())

    def test_late_frame_skips_to_the_next_future_deadline(self):
        from app.view.perf import _SCROLL_FRAME_NS

        class Clock:
            def nsecsElapsed(self):
                return 200_000_000

        self.smoother._elapsed = Clock()
        self.smoother._running = True
        self.smoother._next_frame_ns = _SCROLL_FRAME_NS
        self.smoother._schedule_next()
        self.assertGreater(self.smoother._next_frame_ns, 200_000_000)
        self.assertLessEqual(self.smoother._next_frame_ns - 200_000_000, _SCROLL_FRAME_NS)
        self.assertGreaterEqual(self.smoother._timer.interval(), 1)
        self.assertLessEqual(self.smoother._timer.interval(), 5)
        self.smoother._timer.stop()

    def test_rapid_retarget_does_not_restart_the_pending_frame(self):
        self.send(angle_y=-1)
        timer_id = self.smoother._timer.timerId()
        for _ in range(39):
            self.send(angle_y=-1)
            self.assertEqual(self.smoother._timer.timerId(), timer_id)
        wait_ms(400)
        expected = 500 + round(40 / 120 * self.vbar.singleStep() * QApplication.wheelScrollLines())
        self.assertEqual(self.vbar.value(), expected)
        self.assertFalse(self.smoother._timer.isActive())

    def test_wheel_from_scroll_signal_preserves_both_targets(self):
        repeated = []

        def add_second_wheel(_value):
            if not repeated:
                repeated.append(True)
                self.send(angle_y=-120)

        self.vbar.valueChanged.connect(add_second_wheel)
        try:
            self.send(angle_y=-120)
            wait_ms(450)
        finally:
            self.vbar.valueChanged.disconnect(add_second_wheel)
        self.assertTrue(repeated)
        expected = 500 + 2 * self.vbar.singleStep() * QApplication.wheelScrollLines()
        self.assertEqual(self.vbar.value(), expected)
        self.assertFalse(self.smoother._timer.isActive())

    def test_shrinking_range_clamps_pending_target(self):
        self.vbar.setRange(0, 2000)
        self.vbar.setValue(900)
        self.send(angle_y=-120)
        self.vbar.setMaximum(950)
        wait_ms(350)
        self.assertEqual(self.vbar.value(), 950)
        self.assertEqual(self.smoother._target[0], 950.0)
        self.assertFalse(self.smoother._timer.isActive())
        self.send(angle_y=120)
        wait_ms(350)
        self.assertEqual(self.vbar.value(), 950 - self.vbar.singleStep() * QApplication.wheelScrollLines())

    def test_hiding_from_value_changed_cancels_remaining_tick(self):
        hidden = []

        def hide_area(_value):
            if not hidden:
                hidden.append(True)
                self.area.hide()

        self.vbar.valueChanged.connect(hide_area)
        try:
            self.send(angle_y=-120)
            wait_ms(100)
        finally:
            self.vbar.valueChanged.disconnect(hide_area)
        self.assertTrue(hidden)
        self.assertFalse(self.smoother._running)
        self.assertFalse(self.smoother._timer.isActive())
        self.assertEqual(self.smoother._position, self.smoother._target)

    def test_small_angle_deltas_keep_fractional_distance(self):
        self.vbar.setSingleStep(1)
        for _ in range(40):
            self.send(angle_y=-1)
        wait_ms(350)
        self.assertEqual(self.vbar.value(), 501)
        self.assertFalse(self.smoother._timer.isActive())

    def test_touchpad_pixels_move_immediately_and_cancel_older_target(self):
        self.send(angle_y=-120)
        self.assertTrue(self.smoother._timer.isActive())
        self.send(pixel_y=-24)
        self.assertEqual(self.vbar.value(), 524)
        wait_ms(300)
        self.assertEqual(self.vbar.value(), 524)
        self.assertFalse(self.smoother._timer.isActive())

    def test_horizontal_wheel_and_pixel_scroll(self):
        self.hbar.setSingleStep(10)
        self.send(angle_x=-120)
        wait_ms(400)
        self.assertEqual(self.hbar.value(), 100 + 10 * QApplication.wheelScrollLines())
        self.send(pixel_x=-15)
        self.assertEqual(self.hbar.value(), 115 + 10 * QApplication.wheelScrollLines())

    def test_boundary_and_modified_events_are_left_to_qt(self):
        self.vbar.setValue(self.vbar.maximum())
        self.assertFalse(self.smoother.eventFilter(self.area.viewport(), wheel(self.area, angle_y=-120)))
        self.vbar.setValue(500)
        for modifier in (Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.ShiftModifier):
            event = wheel(self.area, angle_y=-120, modifiers=modifier)
            self.assertFalse(self.smoother.eventFilter(self.area.viewport(), event))
        self.assertEqual(self.vbar.value(), 500)

    def test_external_scroll_and_long_pause_do_not_cause_ghost_movement(self):
        self.send(angle_y=-120)
        self.vbar.setValue(900)
        wait_ms(300)
        self.assertEqual(self.vbar.value(), 900)
        self.send(angle_y=-120)
        time.sleep(0.2)
        wait_ms(80)
        expected = 900 + self.vbar.singleStep() * QApplication.wheelScrollLines()
        self.assertEqual(self.vbar.value(), expected)
        self.assertFalse(self.smoother._timer.isActive())

    def test_reduce_motion_scrolls_without_animation(self):
        D.set_reduce_motion(True)
        self.send(angle_y=-120)
        self.assertEqual(self.vbar.value(), 500 + self.vbar.singleStep() * QApplication.wheelScrollLines())
        self.assertFalse(self.smoother._timer.isActive())

    def test_reduce_motion_finishes_a_scroll_already_in_progress(self):
        self.send(angle_y=-120)
        self.assertTrue(self.smoother._timer.isActive())
        D.set_reduce_motion(True)
        wait_ms(80)
        self.assertEqual(self.vbar.value(), 500 + self.vbar.singleStep() * QApplication.wheelScrollLines())
        self.assertFalse(self.smoother._timer.isActive())


class ScrollCoverageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_fluent_nested_controls_are_tuned_and_native_reader_is_untouched(self):
        root = QWidget()
        controls = [ScrollArea(root), ListWidget(root), TableWidget(root), PlainTextEdit(root)]
        native = QListWidget(root)
        tune_scroll_areas(root)
        for control in controls:
            self.assertTrue(getattr(control, "_perf_wheel_installed", False), type(control).__name__)
            self.assertTrue(control._perf_wheel_filter._timer.isSingleShot())
            self.assertEqual(control._perf_wheel_filter._timer.timerType(), Qt.TimerType.PreciseTimer)
        self.assertFalse(hasattr(native, "_perf_wheel_installed"))
        root.deleteLater()

    def test_icon_badge_cache_refreshes_for_icon_and_theme_changes(self):
        from unittest.mock import patch
        from qfluentwidgets import FluentIcon as FIF, Theme, setTheme
        from qfluentwidgets.common.style_sheet import isDarkTheme
        from app.view import workspace

        original_theme = Theme.DARK if isDarkTheme() else Theme.LIGHT
        original_accent = D.accent_key()
        original_draw = workspace.drawIcon
        calls = []
        badge = workspace.IconBadge(FIF.SEARCH)
        badge.show()
        self.app.processEvents()

        def count_draw(icon, painter, rect):
            calls.append(1)
            original_draw(icon, painter, rect)

        try:
            with patch("app.view.workspace.drawIcon", new=count_draw):
                badge._invalidate_cache()
                badge.grab()
                badge.grab()
                self.assertEqual(len(calls), 1)
                badge.set_icon(FIF.DOWNLOAD)
                badge.grab()
                self.assertEqual(len(calls), 2)
                setTheme(Theme.LIGHT if isDarkTheme() else Theme.DARK)
                badge.grab()
                self.assertEqual(len(calls), 3)
                D.apply_accent("emerald" if original_accent != "emerald" else "deepsea")
                badge.grab()
                self.assertEqual(len(calls), 4)
        finally:
            D.apply_accent(original_accent)
            setTheme(original_theme)
            badge.close()
            badge.deleteLater()
            del badge
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()

    def test_reduce_motion_finishes_inflight_navigation_and_remains_on_new_page(self):
        prior = D.reduce_motion()
        D.set_reduce_motion(False)
        with tempfile.TemporaryDirectory(prefix="_tmp_motion_test_") as tmp:
            config = Config(str(Path(tmp) / "config.json"))
            config.update({"download_path": tmp, "reduce_motion": False})
            window = MainWindow(config)
            window.show()
            self.app.processEvents()
            try:
                window.switchTo(window.settingInterface)
                self.assertEqual(window.stackedWidget.view._ani.state(),
                                 window.stackedWidget.view._ani.State.Running)
                D.set_reduce_motion(True)
                window.switchTo(window.homeInterface)
                self.assertIs(window.stackedWidget.view.currentWidget(), window.homeInterface)
                self.assertFalse(window.stackedWidget.view.isAnimationEnabled)
                wait_ms(300)
                self.assertIs(window.stackedWidget.view.currentWidget(), window.homeInterface)
                self.assertEqual(window.homeInterface.pos(), QPoint(0, 0))
            finally:
                window.close()
                window.deleteLater()
                del window
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                self.app.processEvents()
                D.set_reduce_motion(prior)


if __name__ == "__main__":
    unittest.main()
