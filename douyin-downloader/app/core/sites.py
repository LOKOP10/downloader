# -*- coding: utf-8 -*-
"""多平台单作品解析统一入口与兼容门面。

各平台具体解析实现已拆分至 `app/core/parsers/`（及 `youtube.py`、`dyapi/`、`xhsapi/`），
本模块保留 `extract_url` / `detect_platform` / `parse_share` 路由分发契约及公开解析入口。
"""
import re
import time
import urllib.parse

from .applog import get_logger
from .domain import platform_of_host
from .http_pool import shared_requests_session
from .models import VideoInfo
from .parser import DouyinParser, ParseError
from .parsers._shared import _find_key_recursive
from .parsers.bilibili import _bili_stream_urls, parse_bilibili
from .parsers.hanime1 import hanime1_video_id, parse_hanime1
from .parsers.instagram import (
    _ig_http_cffi_get,
    _instagram_browser,
    _instagram_http_fast,
    parse_instagram,
)
from .parsers.pornhub import parse_pornhub, pornhub_viewkey
from .parsers.x import _TW_GUEST_CACHE, _x_guest_token, parse_x
from .parsers.xiaohongshu import parse_xiaohongshu
from .platform_registry import PLATFORM_ROUTES
from .site_ctx import SiteContext, default_site_context
from .youtube import parse_youtube

__all__ = [
    "SiteContext",
    "default_site_context",
    "detect_platform",
    "extract_url",
    "parse_share",
]

URL_PATTERN = re.compile(r'https?://[^\s，。"\']+', re.I)


def extract_url(text: str) -> str:
    m = URL_PATTERN.search(text or "")
    if not m:
        raise ParseError("未在文本中检测到链接，请粘贴分享文案或链接")
    return m.group(0).rstrip("。.,;")


def detect_platform(url: str) -> str:
    platform = platform_of_host(url)
    if platform:
        return platform
    from .jmcomic_bridge import try_parse_jm
    kind, jmid = try_parse_jm(url or "")
    if jmid:
        return "jmcomic"
    raise ParseError(
        f"暂不支持该链接（{urllib.parse.urlparse(url).netloc}）。目前支持 "
        "抖音 / X / Instagram / B站 / 小红书 / YouTube / Pornhub / "
        "hanime1 / 禁漫")


def parse_share(text: str, ctx: SiteContext = None, **legacy) -> VideoInfo:
    """按链接平台分发解析（成功记一条 info，失败记 error + 堆栈）

    `ctx` 为 SiteContext（推荐）；也兼容旧的按字段传参写法
    （cookie=... / x_cookie=... 等），便于外部脚本继续调用。
    """
    log = get_logger("parse")
    brief = " ".join((text or "").split())[:160]
    t0 = time.time()
    try:
        info = _parse_share(text, ctx, **legacy)
    except Exception as e:  # noqa: BLE001 —— 记完日志再抛给界面
        log.error("解析失败（%.1fs）：%s | 输入=%s",
                  time.time() - t0, e, brief, exc_info=True)
        raise
    log.info("解析成功 %s「%s」用时 %.2fs | 画质 %d 档 | 输入=%s",
             info.source, (info.title or "")[:40], time.time() - t0,
             len(info.quality_options or []), brief)
    return info


def _parse_douyin(url: str, cookie: str, profile_dir: str) -> VideoInfo:
    """把抖音的构造参数与实例方法适配到统一的路由调用。"""
    return DouyinParser(cookie, profile_dir=profile_dir).parse_video(url)


def _parse_jmcomic(text: str, cookie: str) -> VideoInfo:
    """保留禁漫的延迟导入，并在每次调用时取得当前解析函数。"""
    from .jmcomic_bridge import parse_jmcomic
    return parse_jmcomic(text, cookie)


def _parse_share(text: str, ctx: SiteContext = None, **legacy) -> VideoInfo:
    """真正的分发逻辑（说明见 parse_share）"""
    if ctx is None:
        ctx = SiteContext(**{k: v for k, v in legacy.items() if v is not None})
    elif legacy:
        ctx = ctx.replaced(**legacy)
    from .jmcomic_bridge import try_parse_jm

    url = ""
    m = URL_PATTERN.search(text or "")
    if m:
        url = m.group(0).rstrip("。.,;")
    if url:
        platform = platform_of_host(url)
        route = PLATFORM_ROUTES.get(platform)
        if route is not None:
            # 动态解析入口，避免注册时捕获函数导致外部 monkeypatch 失效。
            parser = globals()[route.parser_name]
            return parser(url, *(getattr(ctx, field) for field in route.context_fields))
        # 未知域名但路径像禁漫 /album/ /photo/
        kind, jmid = try_parse_jm(url)
        if jmid:
            return _parse_jmcomic(url, ctx.jm_cookie)
        raise ParseError(
            f"暂不支持该链接（{urllib.parse.urlparse(url).netloc}）。目前支持 "
            "抖音 / X / Instagram / B站 / 小红书 / YouTube / Pornhub / "
            "hanime1 / 禁漫")
    kind, jmid = try_parse_jm(text)
    if jmid:
        return _parse_jmcomic(text, ctx.jm_cookie)
    raise ParseError("未在文本中检测到链接，请粘贴分享文案、链接或禁漫车号（JM123）")
