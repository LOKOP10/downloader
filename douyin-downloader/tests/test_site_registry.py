# -*- coding: utf-8 -*-
"""站点路由离线契约：域名、凭据参数、禁漫兜底和动态解析入口。"""
import unittest
from contextlib import ExitStack
from dataclasses import asdict, fields
from unittest.mock import Mock, patch
from urllib.parse import urlparse

from app.core import domain, jmcomic_bridge, sites
from app.core.models import VideoInfo
from app.core.parser import ParseError
from app.core.platform_registry import PLATFORM_ROUTES
from app.core.site_ctx import SiteContext


OFFICIAL_URLS = {
    "douyin": ("https://www.douyin.com/video/123", "https://v.douyin.com/abc/",
               "https://www.iesdouyin.com/share/video/123/",
               "https://v.douyinvod.com/media", "https://p.douyinpic.com/media"),
    "x": ("https://x.com/user/status/123", "https://twitter.com/user/status/123",
          "https://t.co/abc", "https://video.twimg.com/video.mp4"),
    "instagram": ("https://www.instagram.com/p/abc/",
                  "https://scontent.cdninstagram.com/media", "https://scontent.fbcdn.net/media"),
    "bilibili": ("https://www.bilibili.com/video/BV123", "https://b23.tv/abc",
                 "https://upos.bilivideo.com/media", "https://i.hdslb.com/media",
                 "https://cdn.akamaized.net/media"),
    "xiaohongshu": ("https://www.xiaohongshu.com/explore/123",
                    "https://xhslink.com/abc", "https://xhslink.net/abc",
                    "https://sns.xhscdn.com/media"),
    "jmcomic": ("https://18comic.vip/album/123", "https://jmcomic.me/photo/123",
                "https://jmapinode.vip/album/123", "https://jmapiproxy.org/photo/123",
                "https://jm-comic.net/album/123"),
    "pornhub": ("https://www.pornhub.com/view_video.php?viewkey=abc",
                "https://ev.phncdn.com/media"),
    "hanime1": ("https://hanime1.me/watch?v=123", "https://vdownload.hembed.com/media"),
    "youtube": ("https://www.youtube.com/watch?v=abc", "https://m.youtube.com/watch?v=abc",
                "https://music.youtube.com/watch?v=abc", "https://youtu.be/abc",
                "https://www.youtube-nocookie.com/embed/abc",
                "https://rr1.googlevideo.com/media"),
}


class SiteRegistryContractTest(unittest.TestCase):
    def setUp(self):
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(patch(
            "requests.sessions.Session.request",
            side_effect=AssertionError("路由契约测试不允许网络请求")))
        self.logger = Mock()
        self.patches.enter_context(patch.object(sites, "get_logger", return_value=self.logger))
        self.ctx = SiteContext(
            cookie=" dy=synthetic;\n", x_cookie="x=synthetic",
            ins_cookie="ins=synthetic", ins_profile_dir="profiles/instagram",
            x_profile_dir="profiles/x", dy_profile_dir="profiles/douyin",
            bili_cookie="bili=synthetic", base_dir="application-root",
            xhs_cookie="xhs=synthetic", xhs_profile_dir="profiles/xiaohongshu",
            jm_cookie="jm=synthetic", jm_profile_dir="profiles/jmcomic",
            yt_cookie="yt=synthetic", yt_profile_dir="profiles/youtube")
        self.expected_args = {
            "douyin": (),
            "x": (self.ctx.x_cookie, self.ctx.x_profile_dir),
            "instagram": (self.ctx.ins_cookie, self.ctx.ins_profile_dir),
            "bilibili": (self.ctx.bili_cookie, self.ctx.base_dir),
            "xiaohongshu": (self.ctx.xhs_cookie, self.ctx.xhs_profile_dir),
            "jmcomic": (self.ctx.jm_cookie,),
            "pornhub": (), "hanime1": (),
            "youtube": (self.ctx.yt_cookie, self.ctx.yt_profile_dir),
        }
        self.results = {platform: VideoInfo(source=platform, title="离线作品")
                        for platform in OFFICIAL_URLS}
        self.douyin_factory = self.patches.enter_context(patch.object(sites, "DouyinParser"))
        self.handlers = {"douyin": self.douyin_factory.return_value.parse_video}
        for platform, name in (("x", "parse_x"), ("instagram", "parse_instagram"),
                               ("bilibili", "parse_bilibili"),
                               ("xiaohongshu", "parse_xiaohongshu"),
                               ("pornhub", "parse_pornhub"), ("hanime1", "parse_hanime1"),
                               ("youtube", "parse_youtube")):
            self.handlers[platform] = self.patches.enter_context(patch.object(sites, name))
        self.handlers["jmcomic"] = self.patches.enter_context(
            patch.object(jmcomic_bridge, "parse_jmcomic"))
        for platform, handler in self.handlers.items():
            handler.return_value = self.results[platform]

    def reset_calls(self):
        self.douyin_factory.reset_mock()
        for handler in self.handlers.values():
            handler.reset_mock()

    def assert_dispatch(self, text, platform, expected_input=None):
        self.reset_calls()
        result = sites.parse_share(text, self.ctx)
        self.assertIs(result, self.results[platform])
        self.handlers[platform].assert_called_once_with(
            expected_input if expected_input is not None else text,
            *self.expected_args[platform])
        for other, handler in self.handlers.items():
            if other != platform:
                handler.assert_not_called()
        if platform == "douyin":
            self.douyin_factory.assert_called_once_with(
                self.ctx.cookie, profile_dir=self.ctx.dy_profile_dir)
        else:
            self.douyin_factory.assert_not_called()

    def test_routes_cover_every_detected_platform_and_valid_context_fields(self):
        self.assertEqual(set(PLATFORM_ROUTES), set(domain.PLATFORM_HOSTS))
        names = {field.name for field in fields(SiteContext)}
        for platform, route in PLATFORM_ROUTES.items():
            with self.subTest(platform=platform):
                self.assertTrue(callable(getattr(sites, route.parser_name)))
                self.assertTrue(set(route.context_fields) <= names)

    def test_all_official_hosts_dispatch_with_only_their_original_arguments(self):
        original_context = asdict(self.ctx)
        with patch.object(jmcomic_bridge, "try_parse_jm", side_effect=AssertionError(
                "已识别站点不应进入禁漫兜底")):
            for platform, urls in OFFICIAL_URLS.items():
                for url in urls:
                    with self.subTest(platform=platform, url=url):
                        self.assertEqual(domain.platform_of_host(url), platform)
                        self.assertEqual(sites.detect_platform(url), platform)
                        self.assert_dispatch(url, platform)
        self.assertEqual(asdict(self.ctx), original_context)

    def test_lookalike_and_unknown_hosts_never_receive_a_platform_cookie(self):
        urls = (
            "https://douyin.com.evil.cn/video/1", "https://evildouyin.com/video/1",
            "https://notx.com/status/1", "https://twitter.com.evil.cn/status/1",
            "https://instagram.com.evil.cn/p/abc", "https://evilinstagram.com/p/abc",
            "https://evilbilibili.com/video/BV123", "https://b23.tv.evil.cn/video/1",
            "https://myxiaohongshu.com/explore/1", "https://xhslink.com.evil.cn/abc",
            "https://18comic.vip.evil.cn/view/1", "https://eviljmcomic.vip/view/1",
            "https://pornhub.com.evil.cn/watch/1", "https://notpornhub.com/watch/1",
            "https://hanime1.me.evil.cn/watch/1", "https://evilhembed.com/watch/1",
            "https://youtube.com.evil.cn/watch?v=abc", "https://notyoutube.com/watch?v=abc",
            "https://instagram.com@unknown.example/p/abc", "https://unknown.example/video/1",
        )
        with patch.object(jmcomic_bridge, "try_parse_jm", return_value=("", "")):
            for url in urls:
                with self.subTest(url=url):
                    self.assertEqual(domain.platform_of_host(url), "")
                    self.assertFalse(domain.needs_cookie_for_url(url))
                    expected = (f"暂不支持该链接（{urlparse(url).netloc}）。目前支持 "
                                "抖音 / X / Instagram / B站 / 小红书 / YouTube / Pornhub / "
                                "hanime1 / 禁漫")
                    with self.assertRaises(ParseError) as caught:
                        sites.parse_share(url, self.ctx)
                    self.assertEqual(str(caught.exception), expected)
                    with self.assertRaises(ParseError) as detected:
                        sites.detect_platform(url)
                    self.assertEqual(str(detected.exception), expected)
        for handler in self.handlers.values():
            handler.assert_not_called()
        self.douyin_factory.assert_not_called()

    def test_jm_mirror_paths_codes_and_numeric_inputs_keep_original_payload(self):
        for text in ("https://mirror.example/album/12345",
                     "https://mirror.example/photo/23456", "JM123456",
                     "推荐 jm 123456 这一本", "12 34", "123456"):
            with self.subTest(text=text):
                self.assertEqual(sites.detect_platform(text), "jmcomic")
                self.assert_dispatch(text, "jmcomic")
        # 陌生域禁漫兜底是已有行为，不把它当成普通站点的域名白名单。
        self.assert_dispatch("https://instagram.com.evil.cn/album/12345", "jmcomic")

    def test_recognized_platform_wins_over_jm_path_and_text(self):
        with patch.object(jmcomic_bridge, "try_parse_jm", side_effect=AssertionError(
                "已识别平台必须优先")):
            self.assert_dispatch("https://x.com/album/12345", "x")
            self.assert_dispatch("https://youtube.com/photo/12345", "youtube")
            self.assert_dispatch("https://18comic.vip/view/without-id", "jmcomic")
        unknown = "https://unknown.example/view/abc"
        with patch.object(jmcomic_bridge, "try_parse_jm", return_value=("", "")) as identify:
            with self.assertRaises(ParseError):
                sites.parse_share("JM12345 " + unknown, self.ctx)
            identify.assert_called_once_with(unknown)

    def test_share_text_keeps_first_url_and_original_trailing_punctuation_rules(self):
        url = "https://x.com/user/status/123"
        self.assert_dispatch("分享 " + url + "...; 另一个 https://youtu.be/abc", "x", url)
        self.assertEqual(sites.extract_url("分享 " + url + "...;"), url)
        uppercase = "HTTPS://WWW.INSTAGRAM.COM:443/p/abc/"
        self.assert_dispatch(uppercase, "instagram")

    def test_default_context_and_legacy_keyword_arguments_are_preserved(self):
        url = OFFICIAL_URLS["x"][0]
        self.assertIs(sites.parse_share(url), self.results["x"])
        self.handlers["x"].assert_called_once_with(url, "", "")
        self.handlers["x"].reset_mock()
        sites.parse_share(url, x_cookie="legacy-raw", x_profile_dir="legacy-profile",
                          cookie=None)
        self.handlers["x"].assert_called_once_with(url, "legacy-raw", "legacy-profile")
        self.douyin_factory.assert_not_called()

    def test_legacy_overrides_copy_the_context_and_keep_existing_none_semantics(self):
        original_context = asdict(self.ctx)
        url = OFFICIAL_URLS["x"][0]
        sites.parse_share(url, self.ctx, x_cookie="override", unused_field="ignored")
        self.handlers["x"].assert_called_once_with(url, "override", self.ctx.x_profile_dir)
        self.handlers["x"].reset_mock()
        sites.parse_share(url, self.ctx, x_cookie=None)
        self.handlers["x"].assert_called_once_with(url, None, self.ctx.x_profile_dir)
        self.assertEqual(asdict(self.ctx), original_context)
        with self.assertRaises(TypeError):
            sites.parse_share(url, unknown_context_field="not-accepted-without-ctx")

    def test_invalid_input_preserves_public_and_extract_error_messages(self):
        with patch.object(jmcomic_bridge, "try_parse_jm", return_value=("", "")):
            for text in (None, "", "   ", "没有地址的文案"):
                with self.subTest(text=text), self.assertRaises(ParseError) as caught:
                    sites.parse_share(text, self.ctx)
                self.assertEqual(str(caught.exception),
                                 "未在文本中检测到链接，请粘贴分享文案、链接或禁漫车号（JM123）")
            with self.assertRaises(ParseError) as caught:
                sites.extract_url("没有地址的文案")
            self.assertEqual(str(caught.exception),
                             "未在文本中检测到链接，请粘贴分享文案或链接")
        for handler in self.handlers.values():
            handler.assert_not_called()

    def test_each_parser_error_is_logged_and_reraised_without_fallback(self):
        with patch.object(jmcomic_bridge, "try_parse_jm", side_effect=AssertionError(
                "解析器错误不能触发其他站点兜底")):
            for platform, handler in self.handlers.items():
                with self.subTest(platform=platform):
                    error = ParseError("离线错误：" + platform)
                    handler.side_effect = error
                    self.logger.reset_mock()
                    try:
                        with self.assertRaises(ParseError) as caught:
                            sites.parse_share(OFFICIAL_URLS[platform][0], self.ctx)
                    finally:
                        handler.side_effect = None
                    self.assertIs(caught.exception, error)
                    self.logger.error.assert_called_once()
                    self.assertTrue(self.logger.error.call_args.kwargs["exc_info"])
                    self.logger.info.assert_not_called()

    def test_unexpected_parser_exception_keeps_its_type_and_identity(self):
        error = RuntimeError("offline sentinel")
        self.handlers["youtube"].side_effect = error
        with self.assertRaises(RuntimeError) as caught:
            sites.parse_share(OFFICIAL_URLS["youtube"][0], self.ctx)
        self.assertIs(caught.exception, error)
        self.logger.error.assert_called_once()

    def test_parser_replacements_are_resolved_after_registry_import(self):
        url = OFFICIAL_URLS["x"][0]
        for title in ("第一次替换", "第二次替换"):
            result = VideoInfo(source="x", title=title)
            with patch.object(sites, "parse_x", return_value=result) as current:
                self.assertIs(sites.parse_share(url, self.ctx), result)
                current.assert_called_once_with(url, self.ctx.x_cookie, self.ctx.x_profile_dir)
        for title in ("禁漫替换一", "禁漫替换二"):
            result = VideoInfo(source="jmcomic", title=title)
            with patch.object(jmcomic_bridge, "parse_jmcomic", return_value=result) as current:
                self.assertIs(sites.parse_share("JM12345", self.ctx), result)
                current.assert_called_once_with("JM12345", self.ctx.jm_cookie)


if __name__ == "__main__":
    unittest.main()
