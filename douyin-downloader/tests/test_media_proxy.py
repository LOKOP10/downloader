"""离线验证媒体预览代理的请求头、访问范围和字节区间。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import threading
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from app.core.http_headers import headers_for
from app.core.media_proxy import LocalMediaProxy, media_headers
from app.core.models import VideoInfo

PAYLOAD = b"0123456789abcdef"


class _Upstream(BaseHTTPRequestHandler):
    requests_seen = []

    def log_message(self, *_args):
        pass

    def do_HEAD(self):
        self._serve(True)

    def do_GET(self):
        self._serve(False)

    def _serve(self, head_only):
        self.requests_seen.append((self.command, self.path, self.headers.get("Range"),
                                   self.headers.get("Cookie")))
        if self.path == "/missing":
            self.send_error(404)
            return
        body = PAYLOAD
        byte_range = self.headers.get("Range", "")
        if byte_range and self.path != "/ignore-range":
            first, last = byte_range.removeprefix("bytes=").split("-", 1)
            start = int(first)
            end = int(last) if last else len(PAYLOAD) - 1
            body = PAYLOAD[start:end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(PAYLOAD)}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        if not head_only:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass


class MediaProxyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
        cls.thread = threading.Thread(target=cls.upstream.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.upstream.server_port}"
        cls.opener = build_opener(ProxyHandler({}))

    @classmethod
    def tearDownClass(cls):
        cls.upstream.shutdown()
        cls.upstream.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        _Upstream.requests_seen.clear()
        self.proxy = LocalMediaProxy({"video": [self.base + "/missing", self.base + "/media"],
                                      "audio": [self.base + "/media"]}, "session=test")
        self.addCleanup(self.proxy.stop)

    def request(self, url, method="GET", headers=None):
        return self.opener.open(Request(url, method=method, headers=headers or {}), timeout=5)

    def test_only_token_and_known_track_are_served(self):
        for path in (f"/wrong/{self.proxy.token}/video", "/wrong/video",
                     f"/{self.proxy.token}/unknown"):
            with self.subTest(path=path), self.assertRaises(HTTPError) as caught:
                self.request(f"http://127.0.0.1:{self.proxy.server_port}{path}")
            self.assertEqual(caught.exception.code, 404)
        self.assertEqual(_Upstream.requests_seen, [])

    def test_video_falls_back_and_forwards_range(self):
        with self.request(self.proxy.url("video"), headers={"Range": "bytes=2-5"}) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.headers["Content-Range"], f"bytes 2-5/{len(PAYLOAD)}")
            self.assertEqual(response.read(), PAYLOAD[2:6])
        self.assertEqual([row[1] for row in _Upstream.requests_seen], ["/missing", "/media"])
        self.assertTrue(all(row[2] == "bytes=2-5" for row in _Upstream.requests_seen))
        self.assertTrue(all(row[3] is None for row in _Upstream.requests_seen))

    def test_range_ignoring_source_is_skipped(self):
        self.proxy.tracks["video"] = [self.base + "/ignore-range", self.base + "/media"]
        with self.request(self.proxy.url("video"), headers={"Range": "bytes=1-3"}) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.read(), PAYLOAD[1:4])
        self.assertEqual([row[1] for row in _Upstream.requests_seen], ["/ignore-range", "/media"])

    def test_head_and_audio_track_forward_metadata(self):
        with self.request(self.proxy.url("audio"), method="HEAD") as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(int(response.headers["Content-Length"]), len(PAYLOAD))
            self.assertEqual(response.read(), b"")
        self.assertEqual(_Upstream.requests_seen[0][:2], ("HEAD", "/media"))

    def test_cookie_is_only_attached_to_approved_domains(self):
        allowed = media_headers("https://cdninstagram.com/item", "session=test", "bytes=1-3")
        rejected = media_headers("https://cdninstagram.com.example.org/item", "session=test")
        self.assertEqual(allowed.get("Cookie"), "session=test")
        self.assertEqual(allowed.get("Range"), "bytes=1-3")
        self.assertNotIn("Cookie", rejected)
        self.assertNotIn("Range", rejected)

    def test_preview_dialog_uses_local_video_and_audio_proxy_and_closes_it(self):
        from PySide6.QtWidgets import QApplication
        from app.view.preview_dialog import PreviewDialog

        self.__class__.app = QApplication.instance() or QApplication([])
        video = self.base + "/media"
        info = VideoInfo(title="离线预览", play_url=video, audio_map={video: self.base + "/media"})
        # 保留真实 QMediaPlayer 和 QUrl，不让播放器异步解码测试字节。
        with patch("app.view.preview_dialog.QMediaPlayer.play"), \
                patch("app.view.preview_dialog.QMediaPlayer.setSource", autospec=True) as set_source:
            dialog = PreviewDialog(info, url=video)
            proxy = dialog._proxy
            try:
                sources = [call.args[-1].toString() for call in set_source.call_args_list]
                self.assertEqual(sources, [proxy.url("video"), proxy.url("audio")])
                with self.request(proxy.url("video")) as response:
                    self.assertEqual(response.read(), PAYLOAD)
                self.assertTrue(proxy.thread.is_alive())
            finally:
                dialog.reject()
            self.assertFalse(proxy.thread.is_alive())
            self.assertIsNone(dialog._proxy)

    def test_download_and_preview_headers_remain_consistent(self):
        from app.core import downloader, http_headers

        self.assertIs(downloader.HEADERS, http_headers.HEADERS)
        with patch.dict(downloader.HEADERS, {"User-Agent": "synthetic-agent"}):
            self.assertEqual(media_headers("https://example.com/a")["User-Agent"],
                             "synthetic-agent")

        for url in ("https://example.com/a", "https://cdninstagram.com/a",
                    "https://video.bilibili.com/a", "https://v.douyin.com/a"):
            with self.subTest(url=url):
                self.assertEqual(headers_for(url), downloader.headers_for(url))

    def test_prefetch_serves_large_sequential_range_from_cache(self):
        import time as _time
        large_len = 2 * 1024 * 1024 + 4096
        large_data = (b"ABCD1234" * ((large_len // 8) + 1))[:large_len]
        seen = []

        class _LargeUpstream(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                br = self.headers.get("Range", "")
                seen.append(br)
                first, last = br.removeprefix("bytes=").split("-", 1)
                s = int(first)
                e = int(last) if last else large_len - 1
                chunk = large_data[s:e + 1]
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {s}-{e}/{large_len}")
                self.send_header("Content-Length", str(len(chunk)))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                self.wfile.write(chunk)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), _LargeUpstream)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        p = LocalMediaProxy({"video": [f"http://127.0.0.1:{srv.server_port}/big.mp4"]}, "")
        try:
            with self.request(p.url("video"), headers={"Range": "bytes=0-65535"}) as r1:
                self.assertEqual(r1.read(), large_data[:65536])
            # 等待后台预读块落缓存
            deadline = _time.time() + 3.0
            while _time.time() < deadline:
                if p.get_cached_range("video", 1048576, 1114111) is not None:
                    break
                _time.sleep(0.05)
            before = len(seen)
            with self.request(p.url("video"), headers={"Range": "bytes=1048576-1114111"}) as r2:
                self.assertEqual(r2.read(), large_data[1048576:1114112])
            self.assertEqual(len(seen), before, "第二块应直接命中内存预读缓存")
        finally:
            p.stop()
            srv.shutdown()
            srv.server_close()
            th.join(timeout=2)

    def test_select_preview_option_prefers_smooth_h264_for_4k_av1(self):
        from app.view.preview_dialog import select_preview_option
        info = VideoInfo(
            quality="2160p · 60fps · AV1",
            play_url="https://v/2160",
            quality_options=[
                ("2160p · 60fps · AV1", "https://v/2160"),
                ("1440p · 60fps · AV1", "https://v/1440"),
                ("1080p · 60fps", "https://v/1080"),
                ("720p · 60fps", "https://v/720"),
                ("360p · 30fps", "https://v/360"),
            ],
        )
        lab, url, downgraded = select_preview_option(info, 0)
        self.assertEqual(url, "https://v/720")
        self.assertIn("720p", lab)
        self.assertIn("2160p", downgraded)

        # 选中 1080p 非 AV1 时保持原样不降级
        lab2, url2, downgraded2 = select_preview_option(info, 2)
        self.assertEqual(url2, "https://v/1080")
        self.assertEqual(downgraded2, "")

    def test_preview_dialog_pauses_audio_while_video_buffering(self):
        from PySide6.QtMultimedia import QMediaPlayer
        from PySide6.QtWidgets import QApplication
        from app.view.preview_dialog import PreviewDialog

        self.__class__.app = QApplication.instance() or QApplication([])
        video = self.base + "/media"
        info = VideoInfo(title="缓冲联动", play_url=video, audio_map={video: self.base + "/media"})
        with patch("app.view.preview_dialog.QMediaPlayer.play") as m_play, \
                patch("app.view.preview_dialog.QMediaPlayer.pause") as m_pause, \
                patch("app.view.preview_dialog.QMediaPlayer.setPosition") as m_pos, \
                patch("app.view.preview_dialog.QMediaPlayer.isPlaying", return_value=True), \
                patch("app.view.preview_dialog.QMediaPlayer.position", return_value=4200), \
                patch("app.view.preview_dialog.QMediaPlayer.setSource"):
            dialog = PreviewDialog(info, url=video)
            try:
                m_play.reset_mock()
                m_pause.reset_mock()
                dialog._on_video_status(QMediaPlayer.MediaStatus.BufferingMedia)
                self.assertTrue(dialog._video_buffering)
                m_pause.assert_called()
                dialog._on_video_status(QMediaPlayer.MediaStatus.BufferedMedia)
                self.assertFalse(dialog._video_buffering)
                m_pos.assert_called_with(4200)
                m_play.assert_called()
            finally:
                dialog.reject()


if __name__ == "__main__":
    unittest.main()
