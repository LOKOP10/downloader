"""通过回环 HTTP 服务验证真实下载、信号快照和失败后的批量重试。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QObject, Qt, Slot
from PySide6.QtWidgets import QApplication

from app.core.downloader import DownloadManager

PAYLOAD = bytes(range(256)) * 256


class _MediaServer(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.server.fail:
            self.send_response(503)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        byte_range = self.headers.get("Range")
        if byte_range == "bytes=0-0":
            body = PAYLOAD[:1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes 0-0/{len(PAYLOAD)}")
        else:
            body = PAYLOAD
            self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


class _Receiver(QObject):
    def __init__(self):
        super().__init__()
        self.finished = []

    @Slot(object)
    def on_finished(self, info):
        self.finished.append(info)


class LocalDownloadIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _MediaServer)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/video.mp4"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="_tmp_local_download_")
        self.path = Path(self.temp.name) / "video.mp4"
        self.server.fail = False
        self.manager = DownloadManager(segment_enabled=False)
        self.watchdog = patch.object(self.manager, "_ensure_watchdog")
        self.watchdog.start()
        self.receiver = _Receiver()
        self.manager.task_finished.connect(self.receiver.on_finished, Qt.ConnectionType.QueuedConnection)

    def tearDown(self):
        self.manager.task_finished.disconnect(self.receiver.on_finished)
        self.watchdog.stop()
        self.app.processEvents()
        self.temp.cleanup()

    def wait_finished(self, count):
        deadline = time.monotonic() + 8
        while len(self.receiver.finished) < count and time.monotonic() < deadline:
            self.app.processEvents()
            threading.Event().wait(0.01)
        self.assertEqual(len(self.receiver.finished), count, "未收到下载完成通知")

    def test_local_download_keeps_old_snapshot_and_writes_complete_file(self):
        added = self.manager.add(self.url, str(self.path), "video.mp4")
        self.wait_finished(1)
        done = self.receiver.finished[0]
        self.assertEqual(added.status, "等待中")
        self.assertEqual(done.status, "已完成")
        self.assertEqual(done.downloaded, len(PAYLOAD))
        self.assertEqual(self.path.read_bytes(), PAYLOAD)
        self.assertEqual(self.manager.all_tasks()[0].status, "已完成")

    def test_failed_task_retries_from_finished_snapshot(self):
        self.server.fail = True
        self.manager.add(self.url, str(self.path), "video.mp4")
        self.wait_finished(1)
        failed = self.manager.finished_tasks()[0]
        self.assertEqual(failed.status, "失败")
        self.assertFalse(self.path.exists())
        self.server.fail = False
        self.assertEqual(self.manager.retry_tasks([failed]), 1)
        self.wait_finished(2)
        self.assertEqual([item.status for item in self.receiver.finished], ["失败", "已完成"])
        self.assertEqual(failed.status, "失败")
        self.assertEqual(self.path.read_bytes(), PAYLOAD)


if __name__ == "__main__":
    unittest.main()
