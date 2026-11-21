# -*- coding: utf-8 -*-
"""解析速度优化的离线回归测试：纯 Python 抖音签名、连接池/Cookie缓存、IG/X/YouTube 快路径。"""
import json
import os
import random
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import http_pool, sites, youtube
from app.core.dyapi.abogus import ABogus
from app.core.dyapi.client import _sign_douyin_query, fetch_aweme_detail_http
from app.core.dyapi.sm3 import sm3_hexdigest
from app.core.dyapi.websign import sign as web_sign
from app.core.parser import DouyinParser


class DyApiSigningTest(unittest.TestCase):
    def test_sm3_known_vector(self):
        # 国密 SM3 标准测试向量："abc"
        self.assertEqual(
            sm3_hexdigest(b"abc"),
            "66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0",
        )

    def test_abogus_and_websign_deterministic(self):
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
        bogus1 = ABogus(ua, rng=random.Random(42)).get_value("aweme_id=123", now_ms=1760000000000)
        bogus2 = ABogus(ua, rng=random.Random(42)).get_value("aweme_id=123", now_ms=1760000000000)
        self.assertEqual(bogus1, bogus2)
        self.assertTrue(len(bogus1) > 40)

        q, sig, hdrs = web_sign([("aweme_id", "123")], "uifid_abc", timestamp=1760000000)
        self.assertIn("x-secsdk-web-signature=" + sig, q)
        self.assertEqual(hdrs["uifid"], "uifid_abc")
        self.assertEqual(hdrs["x-secsdk-web-expire"], "1760000000")

    def test_sign_douyin_query_includes_abogus_and_websign(self):
        q, hdrs = _sign_douyin_query(
            {"aweme_id": "123", "msToken": "tok"},
            {"UIFID_TEMP": "temp_uifid", "s_v_web_id": "verify_123"},
            protected=True,
        )
        self.assertIn("a_bogus=", q)
        self.assertIn("verifyFp=verify_123", q)
        self.assertIn("uifid=temp_uifid", q)
        self.assertIn("x-secsdk-web-signature=", q)
        self.assertEqual(hdrs.get("uifid"), "temp_uifid")


class HttpPoolAndCacheTest(unittest.TestCase):
    def test_shared_requests_session_reuses_instance(self):
        s1 = http_pool.shared_requests_session("test_pool_x")
        s2 = http_pool.shared_requests_session("test_pool_x")
        self.assertIs(s1, s2)

    def test_profile_cookie_cache_roundtrip_and_invalidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            http_pool.write_profile_cookie_cache(tmp, "douyin.com", "UIFID_TEMP=abc; ttwid=xyz")
            got = http_pool.read_profile_cookie_cache(tmp, "douyin.com")
            self.assertEqual(got, "UIFID_TEMP=abc; ttwid=xyz")
            http_pool.invalidate_profile_cookie_cache(tmp, "douyin.com")
            self.assertEqual(http_pool.read_profile_cookie_cache(tmp, "douyin.com"), "")

    def test_profile_cookie_cache_persists_beyond_20_hours(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".cookie_cache_douyin_com.json"
            # 模拟 20 小时前写入的缓存（旧 12h TTL 下会被误判失效，现应保持可用）
            import time as _time
            path.write_text(
                json.dumps({"ts": _time.time() - 20 * 3600, "cookie": "UIFID_TEMP=persist_ok"}),
                encoding="utf-8",
            )
            self.assertEqual(
                http_pool.read_profile_cookie_cache(tmp, "douyin.com"),
                "UIFID_TEMP=persist_ok",
            )


class DouyinParserFastPathTest(unittest.TestCase):
    def test_cached_uifid_uses_native_http_without_browser_or_fallback(self):
        item = {
            "aweme_id": "999",
            "desc": "原生签名直连测试",
            "video": {
                "play_addr": {"url_list": ["https://cdn/playwm/1.mp4"]},
                "bit_rate": [{
                    "bit_rate": 2_000_000,
                    "FPS": 60,
                    "play_addr": {"width": 1920, "height": 1080, "url_list": ["https://cdn/1080.mp4"]},
                    "data_size": 123456,
                }],
            },
        }
        share = DouyinParser()._build_info({
            "aweme_id": "999",
            "desc": "分享页基础",
            "video": {"play_addr": {"url_list": ["https://cdn/playwm/low.mp4"]}},
        })
        with tempfile.TemporaryDirectory() as tmp:
            http_pool.write_profile_cookie_cache(tmp, "douyin.com", "UIFID_TEMP=cached_uifid")
            parser = DouyinParser(profile_dir=tmp)
            with patch("app.core.dyapi.fetch_aweme_detail_http", return_value=item) as m_http, \
                    patch.object(parser, "_from_share_page", return_value=share) as m_share, \
                    patch.object(parser, "_from_iteminfo_api") as m_iteminfo, \
                    patch.object(parser, "_from_detail_api") as m_detail, \
                    patch.object(parser, "_from_browser_detail") as m_browser:
                info = parser.parse_video("https://www.douyin.com/video/999")
            self.assertEqual(info.play_url, "https://cdn/1080.mp4")
            self.assertEqual(m_http.call_count, 1)
            m_share.assert_not_called()
            m_iteminfo.assert_not_called()
            m_detail.assert_not_called()
            m_browser.assert_not_called()

    def test_native_http_failure_falls_back_to_browser(self):
        rich = DouyinParser()._build_info({
            "aweme_id": "999",
            "desc": "浏览器补全",
            "video": {"play_addr": {"url_list": ["https://cdn/1080.mp4"]}},
        })
        with tempfile.TemporaryDirectory() as tmp:
            http_pool.write_profile_cookie_cache(tmp, "douyin.com", "UIFID_TEMP=expired")
            parser = DouyinParser(profile_dir=tmp)
            with patch("app.core.dyapi.fetch_aweme_detail_http", return_value=None), \
                    patch.object(parser, "_from_share_page", side_effect=RuntimeError("empty")), \
                    patch.object(parser, "_from_iteminfo_api", side_effect=RuntimeError("empty")), \
                    patch.object(parser, "_from_detail_api", side_effect=RuntimeError("empty")), \
                    patch.object(parser, "_from_browser_detail", return_value=rich) as m_browser:
                info = parser.parse_video("https://www.douyin.com/video/999")
            self.assertEqual(info.play_url, "https://cdn/1080.mp4")
            m_browser.assert_called_once_with("999")

    def test_resolve_stops_at_location_without_downloading_body(self):
        class _FakeCtxResp:
            def __init__(self, headers, url=""):
                self.headers = headers
                self.url = url

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        calls = []

        def fake_get(url, **kwargs):
            calls.append((url, kwargs.get("allow_redirects"), kwargs.get("stream")))
            return _FakeCtxResp(
                {"Location": "https://www.iesdouyin.com/share/video/7709827262971473394/?from=web"},
            )

        parser = DouyinParser()
        with patch.object(parser.session, "get", side_effect=fake_get):
            resolved = parser.resolve("https://v.douyin.com/KNQzca1oHIk/")
        self.assertIn("/share/video/7709827262971473394/", resolved)
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0][1])
        self.assertTrue(calls[0][2])

    def test_warmup_skips_browser_when_uifid_cached(self):
        from app.core import browser_bridge
        with tempfile.TemporaryDirectory() as tmp:
            http_pool.write_profile_cookie_cache(tmp, "douyin.com", "UIFID_TEMP=ready")
            with patch("playwright.sync_api.sync_playwright") as m_pw:
                self.assertTrue(browser_bridge.warmup_douyin_cookies(tmp))
            m_pw.assert_not_called()

    def test_fetch_aweme_detail_http_skips_network_without_uifid(self):
        self.assertIsNone(fetch_aweme_detail_http("999", cookie="", profile_dir=""))


class InstagramAndXAndYoutubeFastPathTest(unittest.TestCase):
    def test_instagram_embed_fast_path_avoids_browser(self):
        embed_ctx = {
            "gql_data": {
                "shortcode_media": {
                    "shortcode": "ABC123",
                    "video_url": "https://cdn.ig/video.mp4",
                    "display_url": "https://cdn.ig/cover.jpg",
                    "dimensions": {"width": 1080, "height": 1920},
                    "owner": {"username": "tester"},
                }
            }
        }
        html = f'"init",[],[{json.dumps({"contextJSON": json.dumps(embed_ctx)})}]],'
        fake_resp = SimpleNamespace(status_code=200, text=html)
        with patch("app.core.sites._ig_http_cffi_get", return_value=fake_resp), \
                patch("app.core.sites._instagram_browser") as m_browser:
            info = sites.parse_instagram("https://www.instagram.com/reel/ABC123/")
        self.assertEqual(info.play_url, "https://cdn.ig/video.mp4")
        self.assertEqual(info.author, "tester")
        m_browser.assert_not_called()

    def test_instagram_reel_never_returns_image_from_fast_path(self):
        embed_ctx = {
            "gql_data": {
                "shortcode_media": {
                    "shortcode": "REEL1",
                    "display_url": "https://cdn.ig/cover.jpg",
                }
            }
        }
        html = f'"init",[],[{json.dumps({"contextJSON": json.dumps(embed_ctx)})}]],'
        fake_resp = SimpleNamespace(status_code=200, text=html)
        with patch("app.core.sites._ig_http_cffi_get", return_value=fake_resp):
            info = sites._instagram_http_fast("REEL1", "", "", is_reel=True)
        self.assertIsNone(info)

    def test_x_guest_token_is_cached(self):
        sites._TW_GUEST_CACHE["token"] = ""
        sites._TW_GUEST_CACHE["ts"] = 0.0
        sess = SimpleNamespace(
            post=lambda *a, **kw: SimpleNamespace(ok=True, json=lambda: {"guest_token": "gt_cached"})
        )
        calls = {"n": 0}
        def counting_post(*a, **kw):
            calls["n"] += 1
            return SimpleNamespace(ok=True, json=lambda: {"guest_token": "gt_cached"})
        sess.post = counting_post
        with patch("app.core.sites.shared_requests_session", return_value=sess):
            t1 = sites._x_guest_token({"User-Agent": "ua"})
            t2 = sites._x_guest_token({"User-Agent": "ua"})
        self.assertEqual(t1, "gt_cached")
        self.assertEqual(t2, "gt_cached")
        self.assertEqual(calls["n"], 1)

    def test_youtube_options_include_cachedir_and_player_client(self):
        with patch("app.core.youtube._proxy_url", return_value=""), \
                patch("app.core.youtube._node_path", return_value=""):
            opts = youtube._ydl_options(15)
        self.assertIn("cachedir", opts)
        self.assertEqual(
            opts["extractor_args"]["youtube"]["player_client"],
            ["android_vr", "ios", "web"],
        )

    def test_bilibili_stream_urls_prefers_upos_over_pcdn_and_keeps_all(self):
        stream = {
            "baseUrl": "https://cn-jsnj-fx-02-01.bilivideo.com/v.m4s?os=bcache",
            "backupUrl": [
                "https://1-2-3-4.mcdn.bilivideo.cn/v.m4s?os=mcdn",
                "https://upos-sz-mirrorcoso1.bilivideo.com/v.m4s?os=coso1bv",
                "https://upos-sz-mirrorcos.bilivideo.com/v.m4s?os=cosbv",
            ],
        }
        urls = sites._bili_stream_urls(stream)
        self.assertEqual(len(urls), 4)
        self.assertTrue(urls[0].startswith("https://upos-sz-mirrorcoso1.bilivideo.com/"))
        self.assertTrue(urls[1].startswith("https://upos-sz-mirrorcos.bilivideo.com/"))
        self.assertIn(stream["baseUrl"], urls[2:])

    def test_downloader_probe_raises_timeout_instead_of_double_waiting(self):
        import requests as _req
        from app.core.downloader import DownloadManager

        with patch("app.core.downloader.requests.get", side_effect=_req.ReadTimeout("slow pcdn")):
            with self.assertRaises(_req.ReadTimeout):
                DownloadManager._probe_total("https://cn-jsnj-fx-02-01.bilivideo.com/v.m4s", {})


if __name__ == "__main__":
    unittest.main()
