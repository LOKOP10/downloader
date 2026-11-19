# -*- coding: utf-8 -*-
"""全局 HTTP 连接池与轻量凭证缓存（纯 Python，不依赖 Qt）。

1. 为 `requests` 提供按业务域隔离的共享 `Session`（增大连接池），避免每次解析重新 TCP/TLS 握手；
2. 为 `curl_cffi` 提供线程级长连接 `Session` 复用与代理同步；
3. 为浏览器持久化 profile 提供跨次解析的 Cookie 快照缓存，使纯 HTTP 路径能直接复用，
   不必每次都启动 Playwright 浏览器。
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Dict, Optional

import requests
from requests.adapters import HTTPAdapter

from .proxy import active_proxies, is_loopback

_REQ_LOCK = threading.Lock()
_REQ_SESSIONS: Dict[str, requests.Session] = {}
_CFFI_LOCAL = threading.local()

_COOKIE_CACHE_TTL = 12 * 3600  # 12 小时内优先复用本地缓存，失败再由浏览器刷新


def shared_requests_session(name: str = "default") -> requests.Session:
    """获取线程安全的共享 requests.Session（带扩大连接池）。"""
    with _REQ_LOCK:
        sess = _REQ_SESSIONS.get(name)
        if sess is None:
            sess = requests.Session()
            adapter = HTTPAdapter(pool_connections=16, pool_maxsize=32)
            sess.mount("http://", adapter)
            sess.mount("https://", adapter)
            _REQ_SESSIONS[name] = sess
        return sess


def cffi_proxies(url: str = "") -> Dict[str, str]:
    """返回 curl_cffi 可用的 proxies 字典；本地回环永不走代理。"""
    if url and is_loopback(url):
        return {}
    return active_proxies()


def shared_cffi_session(name: str = "default", impersonate: str = "chrome"):
    """按线程复用 curl_cffi.requests.Session，减少 TLS 指纹握手开销。"""
    from curl_cffi import requests as cffi_requests

    store = getattr(_CFFI_LOCAL, "sessions", None)
    if store is None:
        store = {}
        _CFFI_LOCAL.sessions = store
    key = (name, impersonate)
    sess = store.get(key)
    if sess is None:
        sess = cffi_requests.Session(impersonate=impersonate)
        store[key] = sess
    return sess


def _cache_file(profile_dir: str, domain_key: str) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in domain_key.strip("."))
    return os.path.join(profile_dir, f".cookie_cache_{safe}.json")


def read_profile_cookie_cache(profile_dir: str, domain_key: str,
                              ttl: int = _COOKIE_CACHE_TTL) -> str:
    """读取上次由浏览器持久化后缓存在本地的 Cookie 字符串；过期或不存在返回空串。"""
    if not profile_dir or not os.path.isdir(profile_dir):
        return ""
    path = _cache_file(profile_dir, domain_key)
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        ts = float(data.get("ts") or 0)
        cookie = str(data.get("cookie") or "").strip()
        if cookie and (time.time() - ts) < ttl:
            return cookie
    except Exception:  # noqa: BLE001
        pass
    return ""


def write_profile_cookie_cache(profile_dir: str, domain_key: str,
                               cookie: str) -> None:
    """把从浏览器上下文提取的 Cookie 缓存到 profile_dir，供后续纯 HTTP 路径免起浏览器复用。"""
    if not profile_dir or not cookie or not os.path.isdir(profile_dir):
        return
    path = _cache_file(profile_dir, domain_key)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"ts": time.time(), "cookie": cookie}, fh, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pass


def invalidate_profile_cookie_cache(profile_dir: str, domain_key: str) -> None:
    """当缓存 Cookie 失效或被风控拒绝时删除缓存文件，触发下次自动刷新。"""
    if not profile_dir:
        return
    path = _cache_file(profile_dir, domain_key)
    try:
        if os.path.isfile(path):
            os.remove(path)
    except Exception:  # noqa: BLE001
        pass
