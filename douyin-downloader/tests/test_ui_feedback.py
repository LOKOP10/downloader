"""界面反馈回归：迟到封面、任务路径、状态与窄窗口操作。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QObject, QPoint, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from qfluentwidgets import TransparentToolButton

from app.core.config import Config
from app.core.models import DownloadTaskInfo, VideoInfo
from app.main_window import MainWindow
from app.view import design as D
from app.view.task_interface import COL_ACTION, COL_NAME


class CoverStub(QObject):
    loaded = Signal(bytes)
    finished = Signal()

    def __init__(self, url, parent=None):
        super().__init__(parent)

    def start(self):
        pass


class UiFeedbackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="_tmp_ui_feedback_")
        self.root = Path(self.temp.name)
        config = Config(str(self.root / "config.json"))
        config.update({"download_path": str(self.root / "downloads"), "reduce_motion": True})
        self.motion = D.reduce_motion()
        D.set_reduce_motion(True)
        self.window = MainWindow(config)
        self.window.show()
        self.app.processEvents()
        self.home = self.window.homeInterface
        self.tasks = self.window.taskInterface

    def tearDown(self):
        self.window.close()
        self.app.processEvents()
        D.set_reduce_motion(self.motion)
        self.temp.cleanup()

    def add_task(self, status="失败"):
        info = DownloadTaskInfo(task_id="test-task", name="测试视频",
                                save_path=str(self.root / "原目录" / "video.mp4"),
                                status=status)
        self.window.ctx.manager._tasks[info.task_id] = info
        self.tasks._on_task_added(info)
        return info

    def test_late_cover_cannot_replace_current_work(self):
        workers = []

        def make_worker(url, parent):
            worker = CoverStub(url, parent)
            workers.append(worker)
            return worker

        with patch("app.view.home_interface.CoverWorker", side_effect=make_worker), patch.object(self.home, "_set_cover") as apply:
            self.home._on_parse_ok(VideoInfo(title="作品 A", cover_url="https://example.com/a.jpg"))
            self.home._on_parse_ok(VideoInfo(title="作品 B", cover_url="https://example.com/b.jpg"))
            workers[1].loaded.emit(b"current")
            workers[0].loaded.emit(b"stale")
            apply.assert_called_once_with(b"current")
            self.home._parse_generation += 1
            workers[1].loaded.emit(b"previous-generation")
            apply.assert_called_once_with(b"current")

    def test_folder_button_uses_current_task_path(self):
        info = self.add_task()
        self.window.switchTo(self.tasks)
        info.save_path = str(self.root / "新目录" / "video.mp4")
        self.tasks._apply_updates([info])
        self.app.processEvents()
        actions = self.tasks.table.cellWidget(0, COL_ACTION)
        button = next(button for button in actions.findChildren(TransparentToolButton)
                      if button.toolTip() == "打开所在文件夹")
        with patch.object(self.tasks, "_open_file_location") as open_location:
            QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        open_location.assert_called_once_with(info.save_path)
        self.assertEqual(self.tasks.table.item(0, COL_NAME).toolTip(), info.save_path)

    def test_cancelled_task_keeps_cleanup_without_failed_retry(self):
        info = self.add_task("已取消")
        self.assertFalse(self.tasks.retryFailedBtn.isEnabled())
        self.assertTrue(self.tasks.clearBtn.isEnabled())
        info.status = "失败"
        self.tasks._refresh_summary()
        self.assertTrue(self.tasks.retryFailedBtn.isEnabled())

    def test_log_toggle_keeps_state_when_page_is_hidden(self):
        self.assertTrue(self.tasks.logView.isHidden())
        self.tasks._toggle_log()
        self.assertFalse(self.tasks.logView.isHidden())
        self.tasks._toggle_log()
        self.assertTrue(self.tasks.logView.isHidden())
        self.window.resize(1024, 620)
        self.window.switchTo(self.tasks)
        self.app.processEvents()
        button = self.tasks.toggleLogBtn
        viewport = self.tasks.viewport()
        self.assertTrue(viewport.rect().contains(button.mapTo(viewport, QPoint())))
        self.assertTrue(viewport.rect().contains(
            button.mapTo(viewport, button.rect().bottomRight())))
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        self.assertTrue(self.tasks.logView.isVisible())

    def test_long_quality_preserves_visible_download_actions(self):
        quality = "2160×3840 · 21000kbps · 60fps · 超长画质说明"
        info = VideoInfo(title="长标题测试，检查小窗口里的下载操作。" * 5,
                         play_url="https://example.com/video.mp4",
                         quality_options=[(quality, "https://example.com/video.mp4")])
        self.home._on_parse_ok(info)
        for width, height in ((1024, 620), (1280, 720)):
            with self.subTest(width=width):
                self.window.resize(width, height)
                self.app.processEvents()
                self.assertLessEqual(self.home.qualityCombo.width(), 360)
                self.assertEqual(self.home.qualityCombo.toolTip(), quality)
                self.assertGreater(self.home.downloadBtn.y(), self.home.qualityCombo.y())
                for button in (self.home.previewBtn, self.home.downloadBtn):
                    top_left = button.mapTo(self.home.viewport(), QPoint())
                    bottom_right = button.mapTo(self.home.viewport(), button.rect().bottomRight())
                    self.assertTrue(self.home.viewport().rect().contains(top_left))
                    self.assertTrue(self.home.viewport().rect().contains(bottom_right))

    def test_conversion_is_counted_as_background_work(self):
        self.add_task("转换中")
        self.window._refresh_status()
        self.assertIn("后台任务 1", self.window.statusBar.leftLabel.text())

    def test_top_theme_switch_updates_settings_selection(self):
        self.window._toggle_theme()
        self.assertEqual(self.window.settingInterface.themeCombo.currentIndex(), 1)
        self.window._toggle_theme()
        self.assertEqual(self.window.settingInterface.themeCombo.currentIndex(), 0)

    def test_open_failure_is_reported(self):
        with patch("app.view.task_interface.open_local_path", return_value=False), patch.object(self.tasks, "_toast") as toast:
            self.tasks._open_path(str(self.root))
        toast.assert_called_once()
        self.assertEqual(toast.call_args.args[0], "无法打开")
        with patch("app.view.task_interface.open_local_path", side_effect=OSError("blocked")), patch.object(self.tasks, "_toast") as toast:
            self.tasks._open_path(str(self.root))
        toast.assert_called_once()


if __name__ == "__main__":
    unittest.main()
