# -*- coding: utf-8 -*-
"""抖音图文动图：解析视频轨，下载完成后转 GIF。"""
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from types import SimpleNamespace

from PySide6.QtCore import QCoreApplication

from app.core.downloader import DownloadManager
from app.core.models import DownloadTaskInfo
from app.core.mp4_to_gif import ConversionCancelled, ConversionError
from app.core.parser import DouyinParser
from app.view.home_interface import HomeInterface


def _static_item():
    item = _live_item()
    for img in item["images"]:
        img.pop("video", None)
    return item


def _live_item():
    return {
        "aweme_id": "7123456789",
        "desc": "动图测试",
        "author": {"nickname": "作者", "uid": 1, "sec_uid": "s"},
        "video": {"cover": {"url_list": ["https://cdn/cover.jpg"]}},
        "images": [
            {"url_list": ["https://cdn/1.jpeg"]},
            {
                "url_list": ["https://cdn/2.jpeg", "https://cdn/2b.jpeg"],
                "video": {"play_addr": {"url_list": [
                    "https://cdn/playwm/2.mp4",
                    "https://cdn/2.mp4",
                ]}},
            },
            {"url_list": ["https://cdn/3.jpeg"], "video": {}},
        ],
    }


class DouyinLiveParseTest(unittest.TestCase):
    def test_extracts_live_video_and_static_images(self):
        info = DouyinParser()._build_info(_live_item())
        self.assertTrue(info.is_image)
        self.assertEqual(info.image_urls, [
            "https://cdn/1.jpeg", "https://cdn/2b.jpeg", "https://cdn/3.jpeg",
        ])
        self.assertEqual(len(info.image_urls), len(info.image_live_url_groups))
        self.assertEqual(info.image_live_url_groups[0], [])
        self.assertEqual(info.image_live_url_groups[1], [
            "https://cdn/play/2.mp4", "https://cdn/2.mp4",
        ])
        # video 存在但 play_addr 为空：仍按静态图处理
        self.assertEqual(info.image_live_url_groups[2], [])

    def test_no_images_keeps_video_path(self):
        item = {
            "aweme_id": "1", "desc": "视频",
            "video": {"play_addr": {"url_list": ["https://cdn/play/1.mp4"]},
                      "cover": {"url_list": ["https://cdn/c.jpg"]}},
        }
        info = DouyinParser()._build_info(item)
        self.assertFalse(info.is_image)
        self.assertEqual(info.play_url, "https://cdn/play/1.mp4")
        self.assertEqual(info.image_live_url_groups, [])


class ParseVideoMergeTest(unittest.TestCase):
    def test_browser_detail_fills_live_groups_for_gallery(self):
        share = DouyinParser()._build_info(_static_item())
        rich = DouyinParser()._build_info(_live_item())
        parser = DouyinParser(profile_dir="dummy")
        with patch.object(parser, "_from_share_page", return_value=share), \
                patch.object(parser, "_from_browser_detail", return_value=rich), \
                patch.object(parser, "parse_ids", return_value=("video", "123")):
            info = parser.parse_video("https://www.douyin.com/video/123")
        self.assertEqual(info.image_urls, share.image_urls)
        self.assertEqual(info.image_live_url_groups,
                         rich.image_live_url_groups)

    def test_keeps_share_when_detail_has_no_live(self):
        share = DouyinParser()._build_info(_static_item())
        rich = DouyinParser()._build_info(_static_item())
        parser = DouyinParser(profile_dir="dummy")
        with patch.object(parser, "_from_share_page", return_value=share), \
                patch.object(parser, "_from_browser_detail", return_value=rich), \
                patch.object(parser, "parse_ids", return_value=("video", "123")):
            info = parser.parse_video("https://www.douyin.com/video/123")
        self.assertEqual(info.image_live_url_groups, [[] for _ in share.image_urls])


class _FakeConfig:
    def __init__(self, base):
        self.base = base

    def get(self, key, default=None):
        if key == "download_path":
            return self.base
        return default

    def build_filename(self, info):
        return "作品"


class _FakeManager:
    def __init__(self):
        self.calls = []

    def add(self, *args, **kwargs):
        self.calls.append((args, kwargs))


class GalleryPlanTest(unittest.TestCase):
    def test_live_image_becomes_gif_task(self):
        info = DouyinParser()._build_info(_live_item())
        with tempfile.TemporaryDirectory() as tmp:
            mgr = _FakeManager()
            page = HomeInterface.__new__(HomeInterface)
            page._current = info
            page.ctx = SimpleNamespace(config=_FakeConfig(tmp), manager=mgr)
            page._toast = lambda *args, **kwargs: None
            page._on_download()
        self.assertEqual(len(mgr.calls), 3)
        folder = os.path.join(tmp, "作品")
        static_call = mgr.calls[0]
        self.assertEqual(static_call[0][0], "https://cdn/1.jpeg")
        self.assertEqual(static_call[0][1], os.path.join(folder, "01.jpeg"))
        live_args, live_kwargs = mgr.calls[1]
        self.assertEqual(live_args[0], "https://cdn/play/2.mp4")
        self.assertEqual(live_args[1], os.path.join(folder, "02_live.mp4"))
        self.assertEqual(live_args[2], f"{info.safe_title(30)}_02.gif")
        self.assertEqual(live_kwargs["fallbacks"], ["https://cdn/2.mp4"])
        self.assertTrue(live_kwargs["convert_to_gif"])
        self.assertEqual(live_kwargs["gif_path"], os.path.join(folder, "02.gif"))
        self.assertEqual(mgr.calls[2][0][0], "https://cdn/3.jpeg")


class GifTaskTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.mgr = DownloadManager(max_concurrent=1)
        self.tmp = tempfile.TemporaryDirectory()
        self.src = os.path.join(self.tmp.name, "01_live.mp4")
        self.gif = os.path.join(self.tmp.name, "01.gif")
        with open(self.src, "wb") as f:
            f.write(b"fake-mp4")

    def tearDown(self):
        self.tmp.cleanup()

    def _info(self):
        return DownloadTaskInfo(
            task_id="t1", url="https://cdn/live.mp4",
            save_path=self.src, name="标题_01.gif",
            convert_to_gif=True, gif_path=self.gif,
            status="已完成", total=8, downloaded=8,
        )

    def test_run_downloads_then_converts_and_removes_video(self):
        info = self._info()
        self.mgr._tasks[info.task_id] = info
        self.mgr._cancel_flags[info.task_id] = threading.Event()
        self.mgr._plans[info.task_id] = [(info.url, "", [])]
        self.mgr._active = 1

        def fake_download(info_, url, cancel, cookies, audio_url, alts):
            info_.status = "已完成"

        def fake_convert(source, destination, *, cancel=None, **kwargs):
            self.assertTrue(os.path.exists(source))
            with open(destination, "wb") as f:
                f.write(b"GIF89a")
            return {"size": 6}

        with patch.object(self.mgr, "_download_once", side_effect=fake_download), \
                patch("app.core.mp4_to_gif.convert", side_effect=fake_convert):
            self.mgr._run(info.task_id)

        self.assertEqual(info.status, "已完成")
        self.assertEqual(info.save_path, self.gif)
        self.assertEqual(info.name, "标题_01.gif")
        self.assertFalse(os.path.exists(self.src))
        self.assertTrue(os.path.exists(self.gif))
        self.assertEqual(info.downloaded, 6)

    def test_conversion_failure_keeps_mp4_and_marks_failed(self):
        info = self._info()
        with patch("app.core.mp4_to_gif.convert",
                   side_effect=ConversionError("ffmpeg 失败")):
            self.mgr._convert_task_gif(info, threading.Event())
        self.assertEqual(info.status, "失败")
        self.assertIn("GIF 转换失败", info.error)
        self.assertTrue(os.path.exists(self.src))
        self.assertFalse(os.path.exists(self.gif))

    def test_conversion_cancel_keeps_mp4(self):
        info = self._info()
        with patch("app.core.mp4_to_gif.convert",
                   side_effect=ConversionCancelled("已取消")):
            self.mgr._convert_task_gif(info, threading.Event())
        self.assertEqual(info.status, "已取消")
        self.assertTrue(os.path.exists(self.src))

    def test_add_carries_gif_fields(self):
        with patch.object(self.mgr, "_pump", lambda: None):
            info = self.mgr.add(
                "https://cdn/live.mp4",
                os.path.join(self.tmp.name, "02_live.mp4"),
                "标题_02.gif",
                convert_to_gif=True,
                gif_path=os.path.join(self.tmp.name, "02.gif"))
        self.assertTrue(info.convert_to_gif)
        self.assertEqual(info.gif_path, os.path.join(self.tmp.name, "02.gif"))


if __name__ == "__main__":
    unittest.main()
