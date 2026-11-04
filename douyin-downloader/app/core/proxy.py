# -*- coding: utf-8 -*-
"""网络代理：跟随系统 / 手动 / 直连，供全项目的 HTTP 请求共用。

为什么必须有这一层：requests 和 curl 都不读 Windows 系统代理，默认一律
DIRECT。用户开着 Clash、只在系统里设了代理时，程序里所有请求仍是直连 ——
iwara 的下载 CDN（robin.iwara.tv）在国内直连几乎建不了连接，表现是「每 4MB
分块 10 秒零字节、反复换连接重连」（2026-11-04 实测：直连 connect 失败、
浏览器走代理则很快）。而抖音 / B站 这类国内 CDN 直连本来就快。

用法：

    proxy.configure(mode, manual)   # 启动时按 config 初始化一次
    proxy.install()                 # 给 requests 打补丁，之后全项目自动生效
    proxy.describe()                # 设置页显示的「当前生效值」

install() 只做一件事：请求未显式传 proxies 时，把当前生效值塞进去。
显式传了的（包括传空字典表示强制直连）一律尊重调用方，本地回环永不走代理。
"""
from __future__ import annotations

import socket
import threading
import time
from typing import Dict, Tuple
from urllib.parse import urlsplit

MODE_SYSTEM = "system"     # 跟随 Windows 系统代理
MODE_MANUAL = "manual"     # 手动填 host:port
MODE_OFF = "off"           # 直连（不走任何代理）
MODES = (MODE_SYSTEM, MODE_MANUAL, MODE_OFF)

MODE_LABELS = {
    MODE_SYSTEM: "跟随系统",
    MODE_MANUAL: "手动",
    MODE_OFF: "直连",
}

_SYSTEM_TTL = 30.0         # 系统代理最多每 30 秒读一次注册表
_lock = threading.Lock()
_mode = MODE_SYSTEM
_manual = ""
_cache: Dict[str, str] = {}
_cache_at = 0.0
_installed = False


def _normalize(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "http://" + text
    return text


def system_proxies() -> Dict[str, str]:
    """读 Windows 系统代理并补齐协议头；读不到返回空字典。"""
    try:
        from common import ProxyBuilder
        raw = ProxyBuilder.system_proxy() or {}
    except Exception:  # noqa: BLE001 - 读不到就当没代理
        return {}
    out: Dict[str, str] = {}
    for key, value in raw.items():
        text = _normalize(value)
        if text:
            out[key] = text
    return out


def normalize_manual(raw) -> str:
    """把手填值规范成 host:port；不合法返回空串。

    只填端口号（如 7897）也算合法 —— 用户看到 Clash 写着「端口 7897」时很
    容易只填数字，若判成非法就只能静默退回直连，表现出来是「填了没生效」。
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    text = text.replace(" ", "")
    lowered = text.lower()
    for prefix in ("http://", "https://", "socks5h://", "socks5://", "socks4://"):
        if lowered.startswith(prefix):
            text = text[len(prefix):]
            break
    text = text.rstrip("/")
    if not text:
        return ""
    if text.isdigit():
        port = int(text)
        return ("127.0.0.1:" + str(port)) if 0 < port <= 65535 else ""
    if ":" not in text:
        return ""
    host, _, port_text = text.rpartition(":")
    host = host.strip().strip("[]")
    port_text = port_text.strip()
    if not host or not port_text.isdigit():
        return ""
    port = int(port_text)
    if not (0 < port <= 65535):
        return ""
    return host + ":" + str(port)


def configure(mode, manual: str = "") -> None:
    """按配置落地模式；每次调用都会清掉系统代理缓存。"""
    global _mode, _manual, _cache, _cache_at
    with _lock:
        _mode = mode if mode in MODES else MODE_SYSTEM
        _manual = str(manual or "")
        _cache = {}
        _cache_at = 0.0


def current_mode() -> str:
    return _mode


def manual_value() -> str:
    return _manual


def probe_reachable(proxy_url: str, timeout: float = 0.4) -> bool:
    """代理端口能不能连上。连不上就当没有代理。

    必须有这一步：系统里的 ProxyEnable 常常残留为 1，而 Clash 已经退出 ——
    这时若照着系统设置走代理，所有请求都会 ProxyError，比直连还糟。
    """
    try:
        parts = urlsplit(str(proxy_url or ""))
        host = parts.hostname
        port = parts.port or 8080
        if not host:
            return False
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:  # noqa: BLE001 - 任何异常都按不可用处理
        return False


def _system_cached() -> Dict[str, str]:
    """系统代理带 30 秒缓存：每个请求都读一次注册表太亏。

    读到代理后还要做一次端口探测：不可达就返回空字典（按直连处理），
    否则 Clash 关着而系统代理还留着时，全项目请求都会失败。
    """
    global _cache, _cache_at
    now = time.time()
    with _lock:
        if _cache and now - _cache_at < _SYSTEM_TTL:
            return dict(_cache)
    value = system_proxies()
    if value:
        target = value.get("http") or value.get("https") or ""
        if target and not probe_reachable(target):
            value = {}
    with _lock:
        _cache = dict(value)
        _cache_at = now
    return value


def invalidate_cache() -> None:
    global _cache, _cache_at
    with _lock:
        _cache = {}
        _cache_at = 0.0


def active_proxies() -> Dict[str, str]:
    """当前生效的 proxies 字典；直连返回空字典。"""
    mode = _mode
    if mode == MODE_OFF:
        return {}
    if mode == MODE_MANUAL:
        value = normalize_manual(_manual)
        if not value:
            return {}
        url = "http://" + value
        return {"http": url, "https": url}
    return _system_cached()


def is_loopback(url: str) -> bool:
    """本地回环地址永不走代理（否则本机服务会被绕出去）。"""
    try:
        host = urlsplit(str(url)).hostname or ""
    except Exception:  # noqa: BLE001
        return False
    host = host.lower()
    if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return True
    return host.startswith("127.")


def install() -> None:
    """给 requests 打补丁：未显式传 proxies 的请求自动带当前生效值。

    幂等，重复调用无副作用。只改「没传 proxies」这一种情况；调用方显式传了
    就完全尊重（包括传空字典表示这条请求强制直连）。
    """
    global _installed
    if _installed:
        return
    try:
        import requests
    except Exception:  # noqa: BLE001 - 没装 requests 就没什么可补的
        return
    original = requests.Session.request

    def request_with_proxy(self, method, url, **kwargs):
        if "proxies" not in kwargs and not is_loopback(url):
            value = active_proxies()
            if value:
                kwargs["proxies"] = value
        return original(self, method, url, **kwargs)

    requests.Session.request = request_with_proxy
    _installed = True


def installed() -> bool:
    return _installed


def describe() -> Tuple[str, bool]:
    """返回（给用户看的一句话，是否真的在走代理）。"""
    mode = _mode
    value = active_proxies()
    if mode == MODE_OFF:
        return "直连（不走代理）", False
    if mode == MODE_MANUAL:
        normalized = normalize_manual(_manual)
        if not normalized:
            return "手动：未填写有效地址，按直连处理", False
        return "手动：" + normalized, True
    if not value:
        return "跟随系统：未检测到可用代理（未设置或端口不通），按直连处理", False
    target = value.get("http") or value.get("https") or ""
    return "跟随系统：" + target.replace("http://", ""), True
