# -*- coding: utf-8 -*-
"""抖音纯 HTTP 高速直连客户端（curl_cffi + a_bogus + websign）。

命中本地缓存的浏览器访客/登录态 Cookie（含 UIFID_TEMP/uifid）或用户手动填写的
Cookie 时，直接通过 HTTP/2 请求抖音 web 接口获取完整作品详情与作者作品分页，
省去启动 headless Edge 的 1.5s~60s 开销；失效或缺失凭证时返回 None 由调用方平滑
回退到浏览器方案。
"""
from __future__ import annotations

import random
import re
import time
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, quote, unquote, urlencode

from ..http_pool import (cffi_proxies, invalidate_profile_cookie_cache,
                         read_profile_cookie_cache, shared_cffi_session)
from ..models import clean_cookie
from .abogus import ABogus, DEFAULT_BROWSER_INFO
from .websign import (VERIFY_FP_COOKIE, VERIFY_FP_PARAMS, encode_pairs,
                      normalize_query, pick_uifid)
from .websign import sign as web_sign

DOUYIN_DOMAIN = "https://www.douyin.com"
DETAIL_API = f"{DOUYIN_DOMAIN}/aweme/v1/web/aweme/detail/"
POST_API = f"{DOUYIN_DOMAIN}/aweme/v1/web/aweme/post/"
PROFILE_API = f"{DOUYIN_DOMAIN}/aweme/v1/web/user/profile/other/"

_CHROME_VERSION = "131.0.0.0"
_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    f"(KHTML, like Gecko) Chrome/{_CHROME_VERSION} Safari/537.36"
)
_MS_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+-"


def _parse_cookies(cookie: str) -> Dict[str, str]:
    kv: Dict[str, str] = {}
    for pair in (cookie or "").split(";"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            k = k.strip()
            if k:
                kv[k] = v.strip()
    return kv


def _merge_cookie_str(primary: str, fallback: str) -> str:
    merged = _parse_cookies(fallback)
    merged.update(_parse_cookies(primary))
    return "; ".join(f"{k}={v}" for k, v in merged.items())


def _resolve_cookie(cookie: str, profile_dir: str) -> Tuple[str, Dict[str, str], bool]:
    """合并传入 Cookie 与本地缓存的浏览器 Cookie，返回 (cookie_str, kv, used_cache)。"""
    cleaned = clean_cookie(cookie or "")
    kv = _parse_cookies(cleaned)
    if pick_uifid(kv):
        return cleaned, kv, False
    cached = clean_cookie(read_profile_cookie_cache(profile_dir, "douyin.com"))
    if not cached:
        return cleaned, kv, False
    merged = _merge_cookie_str(cleaned, cached)
    mkv = _parse_cookies(merged)
    return merged, mkv, True


def _fake_ms_token(length: int = 126) -> str:
    return "".join(random.choice(_MS_ALPHABET) for _ in range(length)) + "=="


def _base_params(ms_token: str) -> Dict[str, str]:
    return {
        "device_platform": "webapp",
        "aid": "6383",
        "channel": "channel_pc_web",
        "pc_client_type": "1",
        "version_code": "290100",
        "version_name": "29.1.0",
        "cookie_enabled": "true",
        "screen_width": "1920",
        "screen_height": "1080",
        "browser_language": "zh-CN",
        "browser_platform": "Win32",
        "browser_name": "Chrome",
        "browser_version": _CHROME_VERSION,
        "browser_online": "true",
        "engine_name": "Blink",
        "engine_version": _CHROME_VERSION,
        "os_name": "Windows",
        "os_version": "10",
        "cpu_core_num": "12",
        "device_memory": "8",
        "platform": "PC",
        "downlink": "10",
        "effective_type": "4g",
        "round_trip_time": "0",
        "update_version_code": "170400",
        "msToken": ms_token,
    }


def _sign_douyin_query(params: Dict[str, str], kv: Dict[str, str],
                       *, protected: bool = True) -> Tuple[str, Dict[str, str]]:
    """按抖音网页 SDK 顺序计算 a_bogus 与 x-secsdk-web-signature。"""
    raw_query = urlencode(params, doseq=True)
    uifid = pick_uifid(kv) if protected else ""
    canonical = normalize_query(raw_query) if uifid else raw_query

    bogus = ABogus(_CHROME_UA, browser_info=DEFAULT_BROWSER_INFO).get_value(canonical)
    signed_query = f"{canonical}&a_bogus={quote(bogus, safe='')}"
    if not uifid:
        return signed_query, {}

    pairs: List[Tuple[str, str]] = []
    for part in signed_query.split("&"):
        if not part:
            continue
        k, _, v = part.partition("=")
        pairs.append((unquote(k), unquote(v)))

    verify_fp = kv.get(VERIFY_FP_COOKIE)
    if verify_fp:
        for name in VERIFY_FP_PARAMS:
            pairs.append((name, verify_fp))

    final_query, _sig, sign_headers = web_sign(pairs, uifid)
    return final_query, sign_headers


def _request_json(endpoint: str, params: Dict[str, str],
                  cookie_str: str, kv: Dict[str, str],
                  referer: str, timeout: int,
                  *, protected: bool = True) -> Tuple[int, Optional[dict]]:
    ms_token = kv.get("msToken") or _fake_ms_token()
    full_params = {**params, **({"msToken": ms_token} if "msToken" not in params else {})}
    query, sign_headers = _sign_douyin_query(full_params, kv, protected=protected)
    url = f"{endpoint}?{query}"

    headers = {
        "User-Agent": _CHROME_UA,
        "Referer": referer,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Cookie": cookie_str,
        **sign_headers,
    }
    sess = shared_cffi_session("douyin", impersonate="chrome")
    proxies = cffi_proxies(url)
    kwargs = {"headers": headers, "timeout": timeout}
    if proxies:
        kwargs["proxies"] = proxies
    resp = sess.get(url, **kwargs)
    if resp.status_code != 200 or not resp.text:
        return resp.status_code, None
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, None


def fetch_aweme_detail_http(aweme_id: str, cookie: str = "",
                            profile_dir: str = "",
                            timeout: int = 12) -> Optional[dict]:
    """纯 HTTP 拉取抖音作品详情 aweme_detail；无可用 uifid 或被拦截时返回 None。"""
    if not aweme_id:
        return None
    cookie_str, kv, used_cache = _resolve_cookie(cookie, profile_dir)
    if not pick_uifid(kv):
        return None
    ms_token = kv.get("msToken") or _fake_ms_token()
    params = {**_base_params(ms_token), "aweme_id": str(aweme_id)}
    referer = f"{DOUYIN_DOMAIN}/video/{aweme_id}"
    try:
        status, data = _request_json(
            DETAIL_API, params, cookie_str, kv, referer, timeout, protected=True
        )
    except Exception:  # noqa: BLE001 - 回退浏览器兜底
        return None

    item = (data or {}).get("aweme_detail") if isinstance(data, dict) else None
    if isinstance(item, dict) and item:
        return item
    if used_cache and status in (401, 403) or (used_cache and not data):
        invalidate_profile_cookie_cache(profile_dir, "douyin.com")
    return None


def fetch_user_posts_http(
    sec_uid: str,
    cookie: str = "",
    profile_dir: str = "",
    max_pages: int = 300,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    timeout: int = 15,
) -> Optional[Tuple[List[dict], str, bool, int]]:
    """纯 HTTP 分页抓取用户主页作品。

    返回 (items, nickname, is_limited, expected_total)；
    若无可用 uifid 或首屏即被拦截返回 None，交给浏览器兜底。
    """
    if not sec_uid:
        return None
    cookie_str, kv, used_cache = _resolve_cookie(cookie, profile_dir)
    if not pick_uifid(kv):
        return None
    ms_token = kv.get("msToken") or _fake_ms_token()
    referer = f"{DOUYIN_DOMAIN}/user/{sec_uid}"

    nickname = ""
    expected = 0
    try:
        p_status, p_data = _request_json(
            PROFILE_API,
            {
                **_base_params(ms_token),
                "sec_uid": sec_uid,
                "publish_video_strategy_type": "2",
                "personal_center_strategy": "1",
            },
            cookie_str, kv, referer, timeout, protected=False,
        )
        if p_status == 200 and isinstance(p_data, dict):
            user = p_data.get("user") or {}
            if user.get("sec_uid") == sec_uid or not user.get("sec_uid"):
                nickname = user.get("nickname") or ""
                expected = int(user.get("aweme_count") or 0)
    except Exception:  # noqa: BLE001
        pass

    items_by_id: Dict[str, dict] = {}
    cursor = "0"
    has_more = True
    done = False
    for _ in range(max_pages):
        params = {
            **_base_params(ms_token),
            "sec_uid": sec_uid,
            "max_cursor": str(cursor),
            "count": "18",
            "publish_video_strategy_type": "2",
            "from_user_page": "1",
            "locate_query": "false",
            "need_time_list": "1",
            "show_live_replay_strategy": "1",
            "time_list_query": "0",
            "pc_libra_divert": "Windows",
            "whale_cut_token": "",
        }
        try:
            status, data = _request_json(
                POST_API, params, cookie_str, kv, referer, timeout, protected=True
            )
        except Exception:  # noqa: BLE001
            break
        if status in (401, 403) or not isinstance(data, dict):
            if used_cache and not items_by_id:
                invalidate_profile_cookie_cache(profile_dir, "douyin.com")
            break
        aweme_list = data.get("aweme_list") or []
        if not aweme_list:
            has_more = bool(data.get("has_more"))
            if not has_more:
                done = True
            break
        for it in aweme_list:
            aid = str(it.get("aweme_id") or "")
            if aid:
                items_by_id[aid] = it
                if not nickname:
                    nickname = ((it.get("author") or {}).get("nickname")) or ""
        if progress_cb:
            progress_cb(len(items_by_id), expected)
        has_more = bool(data.get("has_more"))
        next_cursor = str(data.get("max_cursor") or "0")
        if not has_more or next_cursor == cursor:
            done = not has_more
            break
        cursor = next_cursor

    if not items_by_id:
        return None

    logged_in = bool(kv.get("sessionid") or kv.get("sessionid_ss"))
    # 未登录且平台标明还有更多但被拦截中断时，标记 limited 或交回浏览器补全
    if (not logged_in) and (not done) and len(items_by_id) <= 20:
        limited = True
    else:
        limited = (not logged_in) and (not done)
    return list(items_by_id.values()), nickname, limited, expected
