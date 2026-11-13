"""任务页刷新回归：合成队列与内存日志，不访问真实配置或网络。"""
import logging
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication
from qfluentwidgets import TransparentToolButton

from app.core import applog
from app.core.downloader import DownloadManager
from app.core.models import fmt_speed
from app.view.task_interface import (COL_ACTION, COL_SPEED, COL_STATUS,
                                     LOG_POLL_MS, LOG_VIEW_LINES, TaskInterface)
from app.view.workspace import SegmentedTabs


class TaskRefreshPerfTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="_tmp_task_refresh_")
        self.root = Path(self.temp.name)
        self.memory = applog.MemoryHandler()
        self.memory_patch = patch.dict(applog._state, {"memory": self.memory, "path": "", "dir": ""})
        self.memory_patch.start()
        self.emit_log("startup")
        self.manager = DownloadManager(max_concurrent=1)
        self.pump_patch = patch.object(self.manager, "_pump")
        self.watch_patch = patch.object(self.manager, "_ensure_watchdog")
        self.pump_patch.start()
        self.watch_patch.start()
        ctx = SimpleNamespace(config={"download_path": str(self.root)}, manager=self.manager)
        self.ui = TaskInterface(ctx)
        self.ui.resize(1280, 900)
        self.ui.show()
        self.app.processEvents()

    def tearDown(self):
        self.ui._log_timer.stop()
        self.ui._throttle._timer.stop()
        self.ui.close()
        self.ui.deleteLater()
        self.app.processEvents()
        self.watch_patch.stop()
        self.pump_patch.stop()
        self.memory_patch.stop()
        self.temp.cleanup()

    def emit_log(self, text):
        self.memory.emit(logging.LogRecord("synthetic", logging.INFO, "", 0, text, (), None))

    def add_task(self, status="等待中"):
        number = len(self.manager.all_tasks())
        info = self.manager.add("https://example.invalid/video.mp4",
                                str(self.root / (str(number) + ".mp4")), str(number))
        if status != info.status:
            info.status = status
            self.manager.task_progress.emit(info)
            self.ui._throttle.flush_now()
        return info

    def assert_counts(self, total, active, done, failed):
        self.assertEqual(self.ui.filterTabs._items, ["全部 " + str(total),
                         "进行中 " + str(active), "已完成 " + str(done),
                         "失败 / 取消 " + str(failed)])

    def test_progress_burst_updates_cells_without_scanning_tasks(self):
        info = self.add_task()
        info.total = 1000
        with patch.object(self.manager, "all_tasks", wraps=self.manager.all_tasks) as all_tasks, patch.object(self.ui, "_refresh_summary", wraps=self.ui._refresh_summary) as summary:
            for downloaded in range(1, 201):
                info.downloaded = downloaded
                info.speed = downloaded * 1024
                self.manager.task_progress.emit(info)
            self.ui._throttle.flush_now()
            all_tasks.assert_not_called()
            summary.assert_not_called()
        row = self.ui._rows[info.task_id]
        self.assertEqual(self.ui._progress_bar_at(row).value(), 20)
        self.assertEqual(self.ui.table.item(row, COL_SPEED).text(), fmt_speed(info.speed))
        self.assert_counts(1, 1, 0, 0)
        self.assertEqual(self.ui._throttle._timer.interval(), 120)

    def test_in_place_state_changes_refresh_counts_and_filters_once(self):
        info = self.add_task()
        self.ui.filterTabs.set_current(1, emit=True)
        for status in ("下载中", "合并中", "转换中", "已完成", "失败", "已取消"):
            with self.subTest(status=status):
                info.status = status
                with patch.object(self.ui, "_refresh_summary", wraps=self.ui._refresh_summary) as summary:
                    self.manager.task_progress.emit(info)
                    self.manager.task_finished.emit(info)
                    self.ui._throttle.flush_now()
                    summary.assert_called_once_with()
                active = status in ("下载中", "合并中", "转换中")
                self.assert_counts(1, int(active), int(status == "已完成"), int(status in ("失败", "已取消")))
                row = self.ui._rows[info.task_id]
                self.assertEqual(self.ui.table.item(row, COL_STATUS).text(), status)
                self.assertEqual(self.ui.table.isRowHidden(row), not active)
                self.assertEqual(self.ui.retryFailedBtn.isEnabled(), status == "失败")
                self.assertEqual(self.ui.clearBtn.isEnabled(), not active)

    def test_filter_builds_one_task_snapshot_for_all_rows(self):
        statuses = ("等待中", "下载中", "已完成", "失败", "已取消")
        tasks = [self.add_task(statuses[index % len(statuses)]) for index in range(80)]
        for index, allowed in ((0, None), (1, ("等待中", "下载中", "合并中", "转换中")), (2, ("已完成",)), (3, ("失败", "已取消"))):
            with self.subTest(filter=index), patch.object(self.manager, "all_tasks", wraps=self.manager.all_tasks) as all_tasks:
                self.ui._on_filter_changed(index)
                all_tasks.assert_called_once_with()
                for info in tasks:
                    row = self.ui._rows[info.task_id]
                    self.assertEqual(self.ui.table.isRowHidden(row), allowed is not None and info.status not in allowed)

    def test_cancel_and_clear_finished_keep_rows_and_summary_correct(self):
        cancelled = self.add_task()
        failed = self.add_task("失败")
        actions = self.ui.table.cellWidget(self.ui._rows[cancelled.task_id], COL_ACTION)
        cancel = next(button for button in actions.findChildren(TransparentToolButton)
                      if button.toolTip() == "取消")
        self.assertTrue(cancel.isEnabled())
        QTest.mouseClick(cancel, Qt.MouseButton.LeftButton)
        self.ui._throttle.flush_now()
        self.assertEqual(cancelled.status, "已取消")
        self.assert_counts(2, 0, 0, 2)
        self.manager.retry(failed.task_id)
        self.ui._throttle.flush_now()
        self.assert_counts(2, 1, 0, 1)
        with patch("app.view.task_interface.MessageBox") as confirm:
            confirm.return_value.exec.return_value = True
            self.ui._clear_finished()
        self.assertEqual([task.task_id for task in self.manager.all_tasks()], [failed.task_id])
        self.assertEqual(self.ui.table.rowCount(), 1)
        self.assertEqual(self.ui._rows, {failed.task_id: 0})
        self.assert_counts(1, 1, 0, 0)
        self.assertFalse(self.ui.clearBtn.isEnabled())
        self.manager.cancel(failed.task_id)
        self.ui._throttle.flush_now()
        with patch("app.view.task_interface.MessageBox") as confirm:
            confirm.return_value.exec.return_value = True
            self.ui._clear_finished()
        self.assertEqual(self.ui.table.rowCount(), 0)
        self.assertTrue(self.ui.table.isHidden())
        self.assert_counts(0, 0, 0, 0)

    def test_unchanged_segment_text_does_not_request_repaint(self):
        tabs = SegmentedTabs(["全部 1", "进行中 1"])
        try:
            with patch.object(tabs, "update") as update:
                tabs.set_item_text(0, "全部 1")
                tabs.set_item_text(1, "进行中 1")
                update.assert_not_called()
                tabs.set_item_text(0, "全部 2")
                update.assert_called_once_with()
            self.assertEqual(tabs.accessibleDescription(), "当前选项：全部 2")
        finally:
            tabs.deleteLater()

    def test_folded_log_does_not_build_document_and_opens_with_latest_logs(self):
        changes = QSignalSpy(self.ui.logView.document().contentsChanged)
        self.assertTrue(self.ui.logView.isHidden())
        self.assertEqual(self.ui.logView.toPlainText(), "")
        for number in range(1000):
            self.emit_log("log-" + str(number))
        self.ui._log_timer.timeout.emit()
        self.assertEqual(changes.count(), 0)
        self.assertEqual(self.ui.logView.toPlainText(), "")
        QTest.mouseClick(self.ui.toggleLogBtn, Qt.MouseButton.LeftButton)
        text = self.ui.logView.toPlainText()
        self.assertIn("log-999", text)
        self.assertNotIn("log-0\n", text)
        self.assertGreater(changes.count(), 0)
        self.assertLessEqual(self.ui.logView.document().blockCount(), LOG_VIEW_LINES)
        self.assertEqual(self.ui._log_timer.interval(), LOG_POLL_MS)
        self.assertTrue(self.ui._log_timer.isActive())

    def test_clear_folded_log_discards_startup_backlog(self):
        self.emit_log("before-clear")
        self.ui._clear_log_view()
        self.ui._toggle_log()
        self.assertEqual(self.ui.logView.toPlainText(), "")
        self.emit_log("after-clear")
        self.ui._log_timer.timeout.emit()
        text = self.ui.logView.toPlainText()
        self.assertIn("after-clear", text)
        self.assertNotIn("before-clear", text)
        self.assertNotIn("startup", text)

    def test_reopening_log_appends_once_and_keeps_startup_history(self):
        self.ui._toggle_log()
        self.assertIn("startup", self.ui.logView.toPlainText())
        self.ui._toggle_log()
        self.emit_log("while-folded")
        before = self.ui.logView.toPlainText()
        self.ui._drain_logs()
        self.assertEqual(self.ui.logView.toPlainText(), before)
        self.ui._toggle_log()
        self.assertEqual(self.ui.logView.toPlainText().count("while-folded"), 1)
        self.ui._drain_logs()
        self.ui._toggle_log()
        self.ui._toggle_log()
        self.assertEqual(self.ui.logView.toPlainText().count("while-folded"), 1)

    def test_log_append_preserves_scrolled_up_position(self):
        for number in range(80):
            self.emit_log("history-" + str(number))
        self.ui._toggle_log()
        self.app.processEvents()
        bar = self.ui.logView.verticalScrollBar()
        self.assertGreater(bar.maximum(), 10)
        bar.setValue(0)
        self.app.processEvents()
        self.emit_log("latest")
        self.ui._drain_logs()
        self.app.processEvents()
        self.assertEqual(bar.value(), 0)
        self.assertIn("latest", self.ui.logView.toPlainText())

    def test_hidden_page_catches_up_when_shown(self):
        self.ui._toggle_log()
        self.ui.hide()
        self.emit_log("while-hidden")
        self.ui._drain_logs()
        self.assertNotIn("while-hidden", self.ui.logView.toPlainText())
        self.ui.show()
        self.app.processEvents()
        self.assertIn("while-hidden", self.ui.logView.toPlainText())


if __name__ == "__main__":
    unittest.main()
