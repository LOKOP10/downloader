"""真实 QApplication + mock 转换引擎：MP4 转 GIF 页面队列、参数与生命周期。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtTest import QTest
from PIL import Image
from PySide6.QtWidgets import QApplication

from app.core.config import Config
from app.main_window import MainWindow
from app.view import gif_interface as module
from app.view.gif_interface import GifInterface, _parse_seconds


class GifInterfaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paths = []
        for name in ("乙.mp4", "甲.MP4", "丙 空格.mp4"):
            path = self.root / name
            path.write_bytes(b"mock input")
            self.paths.append(str(path.resolve()))
        config = SimpleNamespace(base_dir=str(self.root),
                                 get=lambda key: str(self.root / "downloads"))
        self.widget = GifInterface(SimpleNamespace(config=config))
        self.errors = []
        hook = patch("sys.excepthook", side_effect=lambda *args: self.errors.append(args))
        hook.start()
        self.addCleanup(hook.stop)
        ffmpeg = patch.object(module, "ffmpeg_path", return_value="local-ffmpeg")
        self.ffmpeg = ffmpeg.start()
        self.addCleanup(ffmpeg.stop)
        self.jobs = []

    def tearDown(self):
        self.widget.shutdown()
        for job in self.jobs:
            self.wait(lambda: not job.thread.is_alive())
        self.widget.close()
        self.app.processEvents()
        self.temp.cleanup()
        self.assertFalse(self.errors, self.errors)

    def wait(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(predicate(), "异步转换未在时限内完成")

    def start(self):
        self.widget.start_conversion()
        if self.widget._job:
            self.jobs.append(self.widget._job)

    def test_defaults_queue_filter_and_deduplicate(self):
        w = self.widget
        self.assertEqual(w.objectName(), "gifInterface")
        self.assertEqual(Path(w.outputEdit.text()), self.root / "downloads" / "converted")
        self.assertFalse(w.overwriteCheck.isChecked())
        self.assertTrue(w.loopCheck.isChecked())
        self.assertEqual(w.fpsCombo.currentIndex(), 1)
        self.assertEqual(w.widthCombo.currentIndex(), 0)
        self.assertEqual(w.startEdit.text(), "0")
        w.add_files(self.paths + self.paths + [str(self.root / "not.txt")])
        self.assertEqual(w.input_paths(), self.paths)
        self.assertIn("忽略", w.statusLabel.text())
        w.inputList.item(1).setSelected(True)
        QTest.mouseClick(w.removeBtn, Qt.MouseButton.LeftButton)
        self.assertEqual(w.input_paths(), [self.paths[0], self.paths[2]])
        QTest.mouseClick(w.clearBtn, Qt.MouseButton.LeftButton)
        self.assertEqual(w.input_paths(), [])

    def test_batch_conversion_passes_options_and_continues_after_failure(self):
        calls = []
        gui_thread = threading.get_ident()

        def engine(source, destination, **kwargs):
            calls.append((source, str(destination), kwargs, threading.get_ident()))
            kwargs["progress_cb"](500, 1000, "mock progress")
            if source == self.paths[0]:
                raise module.ConversionError("broken input")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"GIF89a")
            return dict(width=480, height=270, frames=15, duration_ms=1500, loops=0)

        w = self.widget
        w.add_files(self.paths)
        w.fpsCombo.setCurrentIndex(0)
        w.widthCombo.setCurrentIndex(3)
        w.startEdit.setText("1:02")
        w.durationEdit.setText("5.5")
        w.colorsCombo.setCurrentIndex(2)
        w.ditherCombo.setCurrentIndex(1)
        w.loopCheck.setChecked(False)
        w.overwriteCheck.setChecked(True)
        with patch.object(module, "convert", side_effect=engine):
            self.start()
            self.wait(lambda: not w._busy)
        self.assertEqual([call[0] for call in calls], self.paths)
        self.assertTrue(all(call[3] != gui_thread for call in calls))
        kwargs = calls[0][2]
        self.assertEqual(kwargs["fps"], 10)
        self.assertIsNone(kwargs["width"])
        self.assertEqual(kwargs["start"], 62)
        self.assertEqual(kwargs["duration"], 5.5)
        self.assertEqual(kwargs["colors"], 64)
        self.assertEqual(kwargs["dither"], "none")
        self.assertEqual(kwargs["loop"], -1)
        self.assertTrue(kwargs["overwrite"])
        self.assertEqual(kwargs["ffmpeg"], "local-ffmpeg")
        self.ffmpeg.assert_called_once_with(str(self.root))
        self.assertTrue(calls[0][1].endswith(".gif"))
        self.assertIn("失败", w.resultList.item(0).text())
        self.assertIn("完成", w.resultList.item(1).text())
        self.assertIn("15 帧", w.resultList.item(1).text())
        self.assertTrue(w.startBtn.isEnabled())

    def test_invalid_time_and_missing_ffmpeg_do_not_start(self):
        w = self.widget
        w.add_files(self.paths)
        w.startEdit.setText("abc")
        self.start()
        self.assertFalse(w._busy)
        self.assertIn("时间格式", w.statusLabel.text())
        w.startEdit.setText("-1")
        self.start()
        self.assertFalse(w._busy)
        self.assertIn("不能为负数", w.statusLabel.text())
        w.startEdit.setText("0")
        with patch.object(module, "ffmpeg_path", return_value=None), \
                patch.object(module, "convert") as engine:
            w.start_conversion()
            self.assertIn("设置下载", w.statusLabel.text())
            engine.assert_not_called()
            self.assertFalse(w._busy)

    def test_cancel_stops_later_inputs_and_blocks_resubmission(self):
        entered = threading.Event()

        def engine(source, destination, **kwargs):
            entered.set()
            if not kwargs["cancel"].wait(3):
                raise AssertionError("cancel not signaled")
            raise module.ConversionCancelled("cancelled")

        w = self.widget
        w.add_files(self.paths)
        with patch.object(module, "convert", side_effect=engine) as mocked:
            self.start()
            self.wait(entered.is_set)
            self.assertFalse(w.editPanel.isEnabled())
            self.assertFalse(w.startBtn.isEnabled())
            w.add_files([self.paths[0]])
            w.clear_inputs()
            w.start_conversion()
            self.assertEqual(w.input_paths(), self.paths)
            QTest.mouseClick(w.cancelBtn, Qt.MouseButton.LeftButton)
            self.wait(lambda: not w._busy)
            self.assertEqual(mocked.call_count, 1)
        self.assertTrue(all("已取消" in w.resultList.item(i).text() for i in range(3)))

    def test_close_and_destruction_never_wait_for_engine(self):
        entered = threading.Event()
        released = threading.Event()

        def engine(source, destination, **kwargs):
            entered.set()
            kwargs["cancel"].wait(3)
            released.wait(3)
            raise module.ConversionCancelled("closed")

        w = self.widget
        w.add_files(self.paths)
        with patch.object(module, "convert", side_effect=engine):
            self.start()
            self.wait(entered.is_set)
            job = w._job
            before = time.monotonic()
            w.close()
            self.assertLess(time.monotonic() - before, 0.25)
            self.assertTrue(job.cancel.is_set())
            self.assertTrue(job.thread.is_alive())
            w.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            released.set()
            self.wait(lambda: not job.thread.is_alive())
        self.widget = GifInterface(w.ctx)

    def test_drop_accepts_mp4_and_rejects_other_files(self):
        w = self.widget
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(self.paths[0])])
        enter = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.CopyAction, mime,
                                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(w, enter)
        self.assertTrue(enter.isAccepted())
        drop = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime,
                          Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(w, drop)
        self.assertEqual(w.input_paths(), self.paths[:1])

        text = QMimeData()
        text.setUrls([QUrl.fromLocalFile(str(self.root / "note.txt"))])
        reject = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.CopyAction, text,
                                 Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(w, reject)
        self.assertFalse(reject.isAccepted())

    def test_preview_and_open_output_directory(self):
        def engine(source, destination, **kwargs):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"GIF89a")
            return dict(width=480, height=270, frames=2, duration_ms=200, loops=0)

        w = self.widget
        w.add_files(self.paths[:1])
        with patch.object(module, "convert", side_effect=engine):
            self.start()
            self.wait(lambda: not w._busy)
        w.resultList.setCurrentRow(0)
        self.assertTrue(w.previewBtn.isEnabled())
        with patch.object(module, "open_local_path", return_value=True) as opened:
            QTest.mouseClick(w.previewBtn, Qt.MouseButton.LeftButton)
            self.assertTrue(opened.call_args.args[0].endswith(".gif"))
            QTest.mouseClick(w.openBtn, Qt.MouseButton.LeftButton)
            self.assertEqual(Path(opened.call_args.args[0]), Path(w.outputEdit.text()))

    def test_parse_seconds_formats(self):
        self.assertEqual(_parse_seconds("", 7), 7)
        self.assertEqual(_parse_seconds("12", None), 12)
        self.assertEqual(_parse_seconds("12.5", None), 12.5)
        self.assertEqual(_parse_seconds("1:23", None), 83)
        self.assertEqual(_parse_seconds("01:02:03", None), 3723)
        for text in ("abc", "1:2:3:4", "1:x"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                _parse_seconds(text, None)


class GifWindowIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_main_window_contains_gif_page_after_converter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = Config(str(root / "config.json"))
            cfg.set("download_path", str(root / "downloads"))
            window = MainWindow(cfg)
            try:
                window.show()
                self.app.processEvents()
                self.assertEqual(window.tabBar.count(), 8)
                self.assertEqual(len(window._pages), 8)
                self.assertIs(window._pages[5], window.converterInterface)
                self.assertIs(window._pages[6], window.gifInterface)
                self.assertIs(window._pages[7], window.settingInterface)
                window.switchTo(window.gifInterface)
                self.app.processEvents()
                self.assertEqual(window.stackedWidget.currentWidget(), window.gifInterface)
                self.assertLessEqual(window.tabBar.themeButton.geometry().right(),
                                     window.tabBar.width())
                window.resize(1024, 620)
                self.app.processEvents()
                self.assertTrue(window.tabBar.themeButton.isVisible())
                self.assertLessEqual(window.tabBar.themeButton.geometry().right(),
                                     window.tabBar.width())
            finally:
                window.close()
                self.app.processEvents()

    def test_real_engine_from_main_window(self):
        ffmpeg = Path(__file__).resolve().parents[1] / "dist" / "tools" / "ffmpeg.exe"
        if not ffmpeg.is_file():
            self.skipTest("ffmpeg unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "端到端 视频.mp4"
            with tempfile.TemporaryFile() as log:
                run = subprocess.run(
                    [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
                     "-f", "lavfi", "-i", "color=c=red:s=32x24:d=0.6:r=10",
                     "-pix_fmt", "yuv420p", str(video)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                log.seek(0)
                self.assertEqual(run.returncode, 0, log.read().decode("utf-8", "replace"))
            cfg = Config(str(root / "config.json"))
            cfg.set("download_path", str(root / "downloads"))
            window = MainWindow(cfg)
            try:
                window.show()
                page = window.gifInterface
                window.switchTo(page)
                page.add_files([str(video)])
                page.outputEdit.setText(str(root / "outputs"))
                page.fpsCombo.setCurrentIndex(0)
                page.widthCombo.setCurrentIndex(1)
                with patch("app.view.gif_interface.ffmpeg_path", return_value=str(ffmpeg)):
                    page.start_conversion()
                    deadline = time.monotonic() + 20
                    while page._busy and time.monotonic() < deadline:
                        self.app.processEvents()
                        time.sleep(0.01)
                self.assertFalse(page._busy)
                self.assertTrue(page.resultList.item(0).text().startswith("完成"))
                output = root / "outputs" / "端到端 视频.gif"
                self.assertTrue(output.is_file())
                with Image.open(output) as image:
                    self.assertEqual(image.format, "GIF")
                    self.assertEqual(image.size[0], 320)
                    self.assertGreaterEqual(getattr(image, "n_frames", 1), 4)
            finally:
                window.close()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
