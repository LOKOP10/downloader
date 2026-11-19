"""验证任务仍在收尾或确认期间发生变化时不会误删文件。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from app.core.downloader import DownloadManager
from app.core.models import DownloadTaskInfo
from app.view.task_interface import TaskInterface


class TaskFileSafetyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="_tmp_task_file_safety_")
        self.root = Path(self.temp.name)
        self.path = self.root / "video.mp4"
        self.path.write_bytes(b"synthetic video")
        self.manager = DownloadManager()
        self.info = DownloadTaskInfo(task_id="file-safety", url="https://example.invalid/v",
                                     name="video.mp4", save_path=str(self.path), status="已完成")
        self.manager._tasks[self.info.task_id] = self.info
        self.pump = patch.object(self.manager, "_pump")
        self.pump.start()
        self.ui = TaskInterface(SimpleNamespace(config={"download_path": str(self.root)},
                                                manager=self.manager))

    def tearDown(self):
        self.ui.close()
        self.ui.deleteLater()
        self.app.processEvents()
        self.pump.stop()
        self.temp.cleanup()

    def test_active_worker_cannot_delete_its_finished_looking_file(self):
        self.manager._cancel_flags[self.info.task_id] = threading.Event()
        with patch("app.view.task_interface.MessageBox") as confirm, patch.object(self.ui, "_toast") as toast:
            self.ui._on_delete_file(self.info.task_id)
        confirm.assert_not_called()
        self.assertEqual(toast.call_args.args[0], "无法删除")
        self.assertTrue(self.path.exists())
        self.assertEqual(len(self.manager.all_tasks()), 1)

    def test_retry_during_confirmation_preserves_file(self):
        with patch("app.view.task_interface.MessageBox") as confirm, patch.object(self.ui, "_toast") as toast:
            def retry_before_confirmation():
                self.manager.retry(self.info.task_id)
                return True
            confirm.return_value.exec.side_effect = retry_before_confirmation
            self.ui._on_delete_file(self.info.task_id)
        self.assertEqual(toast.call_args.args[0], "任务状态已变化")
        self.assertTrue(self.path.exists())
        self.assertEqual(self.manager.all_tasks()[0].status, "等待中")

    def test_changed_path_during_confirmation_preserves_both_files(self):
        other = self.root / "other.mp4"
        other.write_bytes(b"other")
        with patch("app.view.task_interface.MessageBox") as confirm, patch.object(self.ui, "_toast") as toast:
            def move_before_confirmation():
                self.manager._update_task(self.info, save_path=str(other))
                return True
            confirm.return_value.exec.side_effect = move_before_confirmation
            self.ui._on_delete_file(self.info.task_id)
        self.assertEqual(toast.call_args.args[0], "任务状态已变化")
        self.assertTrue(self.path.exists())
        self.assertTrue(other.exists())

    def test_completed_task_can_still_be_deleted_after_confirmation(self):
        with patch("app.view.task_interface.MessageBox") as confirm, patch.object(self.ui, "_toast"):
            confirm.return_value.exec.return_value = True
            self.ui._on_delete_file(self.info.task_id)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.manager.all_tasks(), [])


if __name__ == "__main__":
    unittest.main()
