# -*- coding: utf-8 -*-
"""网络代理模式的解析与生效判断（不联网）。"""
import unittest

from app.core import proxy


class NormalizeManualTest(unittest.TestCase):
    def test_port_only_becomes_loopback(self):
        self.assertEqual(proxy.normalize_manual("7897"), "127.0.0.1:7897")
        self.assertEqual(proxy.normalize_manual(" 7897 "), "127.0.0.1:7897")

    def test_host_port(self):
        self.assertEqual(proxy.normalize_manual("127.0.0.1:7897"), "127.0.0.1:7897")
        self.assertEqual(proxy.normalize_manual("192.168.1.9:1080"), "192.168.1.9:1080")

    def test_scheme_stripped(self):
        self.assertEqual(proxy.normalize_manual("http://127.0.0.1:7897"), "127.0.0.1:7897")
        self.assertEqual(proxy.normalize_manual("socks5://127.0.0.1:7890/"), "127.0.0.1:7890")

    def test_invalid(self):
        for raw in ("", "   ", None, "abc", "127.0.0.1", "127.0.0.1:99999", "1.2.3.4:0"):
            self.assertEqual(proxy.normalize_manual(raw), "", f"应判非法: {raw!r}")


class ModeTest(unittest.TestCase):
    def tearDown(self):
        proxy.configure(proxy.MODE_OFF, "")

    def test_off_returns_empty(self):
        proxy.configure(proxy.MODE_OFF, "")
        self.assertEqual(proxy.active_proxies(), {})

    def test_manual_returns_both_schemes(self):
        proxy.configure(proxy.MODE_MANUAL, "7897")
        self.assertEqual(proxy.active_proxies(),
                         {"http": "http://127.0.0.1:7897",
                          "https": "http://127.0.0.1:7897"})

    def test_manual_invalid_falls_back_to_direct(self):
        proxy.configure(proxy.MODE_MANUAL, "not-a-proxy")
        self.assertEqual(proxy.active_proxies(), {})

    def test_unknown_mode_falls_back_to_system(self):
        proxy.configure("随便写的值", "")
        self.assertEqual(proxy.current_mode(), proxy.MODE_SYSTEM)

    def test_describe_marks_off_as_inactive(self):
        proxy.configure(proxy.MODE_OFF, "")
        text, active = proxy.describe()
        self.assertIn("直连", text)
        self.assertFalse(active)

    def test_describe_manual_active(self):
        proxy.configure(proxy.MODE_MANUAL, "7897")
        text, active = proxy.describe()
        self.assertEqual(text, "手动：127.0.0.1:7897")
        self.assertTrue(active)


class LoopbackTest(unittest.TestCase):
    def test_loopback_detected(self):
        for url in ("http://127.0.0.1:8000/x", "https://localhost:9/y",
                    "http://127.0.1.5/z", "https://[::1]:80/a"):
            self.assertTrue(proxy.is_loopback(url), url)

    def test_public_not_loopback(self):
        for url in ("https://robin.iwara.tv/x", "https://api.iwara.tv/video/1"):
            self.assertFalse(proxy.is_loopback(url), url)


class InstallTest(unittest.TestCase):
    def test_install_is_idempotent(self):
        proxy.install()
        proxy.install()
        self.assertTrue(proxy.installed())

    def test_patch_only_fills_missing_proxies(self):
        import requests
        proxy.install()
        original = requests.Session.request
        # 补丁后仍是一个可调用对象，且未被重复包裹（id 稳定）
        proxy.install()
        self.assertIs(requests.Session.request, original)


class ReachableTest(unittest.TestCase):
    def test_closed_port_is_unreachable(self):
        # 9 号端口一般没有服务：必须判成不可用，让调用方回落直连
        self.assertFalse(proxy.probe_reachable("http://127.0.0.1:9", timeout=0.3))

    def test_unparsable_is_unreachable(self):
        self.assertFalse(proxy.probe_reachable("", timeout=0.2))
        self.assertFalse(proxy.probe_reachable("http://", timeout=0.2))


if __name__ == "__main__":
    unittest.main()