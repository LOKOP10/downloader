# -*- coding: utf-8 -*-
"""X (Twitter) 单作品解析（syndication / GraphQL / 浏览器兜底）。"""
import json
import re
import threading
import time

import requests

from ._shared import _find_key_recursive
from ..domain import referer_for
from ..http_pool import shared_requests_session
from ..models import VideoInfo, clean_cookie
from ..parser import PC_UA, ParseError


# --------------------------------------------------------------------- #
# X (Twitter)
# --------------------------------------------------------------------- #
TW_BEARER = ("AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
             "=1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA")
# 与 x.com 前端同步的 GraphQL 端点（queryId 会随 X 前端更新轮换）
# 轮换后旧 id 会返回 HTTP 400 / 404；下方 _refresh_gql_query_id() 会在
# 失败时自己从页面流量里抓新的 id 并缓存，所以不必每次手动改这里。
TW_GQL_ENDPOINT = ("https://api.x.com/graphql/"
                   "Xl0tsHf4AzflMRjbw9e70A/TweetResultByRestId")

# queryId 候选表（官方前端历史上用过的值）。
# 轮换后逐个用纯 HTTP 试，全部失败才去开无头浏览器现场抓 —— 浏览器
# 一次要 3~5 秒，是 X 解析里最慢的一环。
TW_GQL_QUERY_IDS = (
    "Xl0tsHf4AzflMRjbw9e70A",   # 2025-xx 前端在用
    "2ICDjqPd81tulZcYrtpTuQ",   # 旧值（保留作兜底，轮换初期可能仍有效）
    "nBS-WpgA6ZG0CyNHD517JQ",
    "xOhkmRac04YFZmOzU9PJHg",
)

# 运行时捕获到的新 queryId（模块级缓存，避免每次都去开浏览器）
_GQL_QUERY_ID_CACHE = {"id": ""}

_GQL_QUERY = {
    "variables": {
        "tweetId": "",
        "withCommunity": False,
        "includePromotedContent": False,
        "withVoice": False,
    },
    "features": {
        "creator_subscriptions_tweet_preview_api_enabled": True,
        "tweetypie_unmention_optimization_enabled": True,
        "responsive_web_edit_tweet_api_enabled": True,
        "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
        "view_counts_everywhere_api_enabled": True,
        "longform_notetweets_consumption_enabled": True,
        "responsive_web_twitter_article_tweet_consumption_enabled": False,
        "tweet_awards_web_tipping_enabled": False,
        "freedom_of_speech_not_reach_fetch_enabled": True,
        "standardized_nudges_misinfo": True,
        "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
        "longform_notetweets_rich_text_read_enabled": True,
        "longform_notetweets_inline_media_enabled": True,
        "responsive_web_graphql_exclude_directive_enabled": True,
        "verified_phone_label_enabled": False,
        "responsive_web_media_download_video_enabled": False,
        "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
        "responsive_web_graphql_timeline_navigation_enabled": True,
        "responsive_web_enhance_cards_enabled": False,
    },
    "fieldToggles": {"withArticleRichContentState": False},
}


def _gql_endpoint(query_id: str = "") -> str:
    """拼出 GraphQL 端点地址（query_id 为空时用模块默认值）"""
    qid = query_id or _GQL_QUERY_ID_CACHE.get("id") or ""
    if not qid:
        # 从 TW_GQL_ENDPOINT 里把 id 抠出来
        parts = TW_GQL_ENDPOINT.rstrip("/").split("/")
        qid = parts[-2] if len(parts) >= 2 else ""
    return f"https://api.x.com/graphql/{qid}/TweetResultByRestId"


def _refresh_gql_query_id(profile_dir: str, tweet_id: str) -> str:
    """开一次无头浏览器，从页面真实流量里抓取当前有效的 GraphQL queryId

    X 会不定期轮换 queryId，写在代码里的常量一旦过期，接口就返回
    HTTP 400（`{"errors":[{"message":"Bad Request"}]}`）或 404，表现为
    "Cookie 可能已过期"这种误导性报错。这里直接从浏览器实际发出的
    `/i/api/graphql/<id>/TweetResultByRestId` 请求里取 id，并缓存后续复用。
    """
    if not profile_dir:
        return ""
    cached = _GQL_QUERY_ID_CACHE.get("id") or ""
    if cached:
        return cached
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ""
    found = {}
    try:
        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(
                profile_dir, channel="msedge", headless=True,
                args=["--disable-blink-features=AutomationControlled"])
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()

                def on_resp(resp):
                    u = resp.url or ""
                    if "/graphql/" in u and "TweetResultByRestId" in u:
                        seg = u.split("/graphql/", 1)[1].split("/")
                        if len(seg) >= 2 and seg[0]:
                            found.setdefault("id", seg[0])

                page.on("response", on_resp)
                try:
                    page.goto(f"https://x.com/i/status/{tweet_id}",
                              wait_until="domcontentloaded", timeout=45000)
                    # 页面自己会发 GraphQL 请求，轮询到即走（原先固定等 8s）
                    waited = 0
                    while not found and waited < 8000:
                        page.wait_for_timeout(200)
                        waited += 200
                except Exception:  # noqa: BLE001 - 拿不到就算了
                    pass
            finally:
                ctx.close()
    except Exception:  # noqa: BLE001
        return ""
    qid = found.get("id") or ""
    if qid:
        _GQL_QUERY_ID_CACHE["id"] = qid
    return qid


def _syndication_token(twid: str) -> str:
    """syndication 接口 token：((id/1e15)*PI).toString(36) 去掉 0 和 ."""
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    value = (int(twid) / 1e15) * 3.141592653589793
    int_part = int(value)
    frac = value - int_part
    s = ""
    n = int_part
    if n == 0:
        s = "0"
    while n > 0:
        s = digits[n % 36] + s
        n //= 36
    if frac > 0:
        s += "."
        for _ in range(20):
            frac *= 36
            d = int(frac)
            s += digits[d]
            frac -= d
            if frac <= 0:
                break
    return s.replace("0", "").replace(".", "")


def _x_quality_label(url: str, bitrate: int) -> str:
    """从 twimg 地址路径中解析分辨率，拼成画质标签"""
    m = re.search(r'/(\d{2,5})x(\d{2,5})/', url)
    label = f"{m.group(1)}×{m.group(2)}" if m else ""
    if bitrate:
        label = f"{label} · {bitrate // 1000}kbps" if label else f"{bitrate // 1000}kbps"
    return label or "视频"


def _x_clean_og_title(text: str) -> str:
    """清掉 X 页面标题里的噪声前缀与尾部附着内容

    浏览器标题通常是 `(2) X 上的 作者："正文" / X` 这种形状：
    - 开头的 `(2)` 是未读通知数，每个人不一样，且会变
    - `X 上的 <昵称>：` 是站点的标题模板
    - 正文里还会粘着推文自身的 t.co 短链（跟正文重复）
    """
    if not text:
        return ""
    s = text.strip()
    # 先剥掉外层成对的引号（og 描述里正文被引号包着）
    for _ in range(2):
        s = s.strip()
        if len(s) >= 2 and (s[0], s[-1]) in (('"', '"'), ("\u201c", "\u201d"),
                                            ("'", "'")):
            s = s[1:-1]
    # 去掉开头的未读计数 "(2) " / "[2] "
    s = re.sub(r'^[\(\[]\d+[\)\]]\s*', "", s)
    # 去掉 "X 上的 <作者>：" / "<作者> on X: " 模板前缀
    s = re.sub(r'^X\s*上的\s*[^：:]{0,40}[：:]\s*', "", s)
    s = re.sub(r'^.{0,40}\s+on\s+X\s*[:：]\s*', "", s, flags=re.I)
    # 去掉尾部的 " / X"
    s = re.sub(r'\s*/\s*X\s*$', "", s)
    # 去掉正文里粘着的 t.co 短链（与正文重复）：扫描到链接就截断
    s = re.split(r'\s*https?://t\.co/\w+', s)[0]
    return s.strip().strip('"“”').strip()


def _tw_build_info(status: dict, user_legacy: dict, tweet_id: str) -> VideoInfo:
    """从 legacy 风格 tweet 对象构建 VideoInfo"""
    info = VideoInfo()
    info.source = "x"
    info.aweme_id = tweet_id
    info.title = _x_clean_og_title(
        status.get("full_text") or status.get("text") or "")
    info.author = (user_legacy.get("name")
                   or user_legacy.get("screen_name") or "")
    info.digg_count = int(status.get("favorite_count", 0) or 0)
    info.comment_count = int(status.get("reply_count", 0) or 0)

    entities = status.get("extended_entities") or status.get("entities") or {}
    for media in entities.get("media") or []:
        vinfo = media.get("video_info") or {}
        variants = vinfo.get("variants") or []
        if variants:
            mp4s = [(int(v.get("bitrate") or 0), v["url"])
                    for v in variants
                    if (v.get("content_type") or "").startswith("video/mp4")
                    and v.get("url")]
            if mp4s:
                mp4s.sort(key=lambda x: x[0], reverse=True)
                dur_ms = int(vinfo.get("duration_millis", 0) or 0)
                options, seen, sizes = [], set(), {}
                for rate, url in mp4s:
                    if url not in seen:
                        seen.add(url)
                        options.append((_x_quality_label(url, rate), url))
                        # X 无字节数，用码率 × 时长估算
                        if rate > 0 and dur_ms > 0:
                            sizes[url] = int(rate * (dur_ms / 1000.0) / 8.0)
                info.quality_options = options
                info.quality_sizes = sizes
                info.play_url_candidates = [u for _, u in options]
                info.play_url = options[0][1]
                info.quality = options[0][0]
                info.duration = dur_ms
        elif media.get("media_url_https"):
            info.image_urls.append(media["media_url_https"])
        if not info.cover_url:
            info.cover_url = media.get("media_url_https", "")

    if not info.play_url and not info.image_urls:
        raise ParseError("该推文不包含可下载的视频或图片")
    if info.image_urls and not info.play_url:
        info.is_image = True
    return info


def _tw_cookie_kv(x_cookie: str) -> dict:
    kv = {}
    for pair in clean_cookie(x_cookie).split(";"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            kv[k.strip()] = v.strip()
    return kv


def parse_x(url: str, x_cookie: str = "",
            x_profile_dir: str = "") -> VideoInfo:
    """解析 X 推文：syndication → GraphQL（Cookie/游客）→ 浏览器渲染回退"""
    m = re.search(r'/status(?:es)?/(\d+)', url)
    if not m:
        raise ParseError("无法从链接中识别推文 ID")
    tweet_id = m.group(1)

    info = None
    try:
        info = _x_api(tweet_id, x_cookie, x_profile_dir)
    except ParseError as api_err:
        # 浏览器渲染回退：使用持久化登录态（推荐路径，无需手填 Cookie）
        if x_profile_dir:
            try:
                info = _x_browser(tweet_id, x_profile_dir)
            except ParseError:
                info = None
        if info is None:
            raise api_err
    _x_fill_exact_sizes(info)
    return info


def _x_fill_exact_sizes(info: VideoInfo) -> None:
    """用 HEAD 取每个档位的精确字节数，替换「码率 × 时长」估算

    ⚠️ X 的 `variants[].bitrate` 是**峰值**码率，不是平均码率：
    实测同一条 1080×1800 / 204.3s 的视频标 10368kbps，但实际文件只有
    112.8MB（平均 4413kbps）—— 直接乘时长会高估 2 倍以上，用户会看到
    「预测 252MB、实际只下了 100 多 MB」，误以为没下全。
    CDN 支持 HEAD（实测无 Referer 也回 Content-Length），4 个档位并行取，
    约 0.2~0.3s；拿不到就保留原估算值。
    """
    opts = list(info.quality_options or [])
    urls = [u for _lab, u in opts if u]
    if not urls:
        return

    sizes = {}
    lock = threading.Lock()

    def probe(u):
        headers = {"User-Agent": PC_UA, "Referer": referer_for(u)}
        sess = shared_requests_session("x")
        n = 0
        try:
            r = sess.head(u, headers=headers, timeout=10,
                          allow_redirects=True)
            if r.status_code == 200:
                n = int(r.headers.get("content-length") or 0)
            if n <= 0:
                # 少数边缘节点对 HEAD 不回 Content-Length：
                # 退回「1 字节 Range」，从 Content-Range 里读总长度
                r = sess.get(u, headers=dict(headers, Range="bytes=0-0"),
                             timeout=10, allow_redirects=True)
                m2 = re.search(r'/(\d+)\s*$',
                               r.headers.get("Content-Range") or "")
                n = int(m2.group(1)) if m2 else 0
                if n <= 0 and r.status_code == 200:
                    n = int(r.headers.get("content-length") or 0)
        except Exception:  # noqa: BLE001 - 拿不到就退回估算
            n = 0
        if n > 0:
            with lock:
                sizes[u] = n

    jobs = [threading.Thread(target=probe, args=(u,), daemon=True)
            for u in dict.fromkeys(urls)]
    for job in jobs:
        job.start()
    for job in jobs:
        job.join(timeout=12)
    if not sizes:
        return

    info.quality_sizes.update(sizes)
    # 码率也换成「按实际字节数算出的平均码率」，否则标签里的 kbps
    # 和后面的 MB 对不上（同一条视频会显示 10368kbps · 107.6MB）
    dur_ms = int(info.duration or 0)
    if dur_ms > 0:
        info.quality_options = [
            (_x_quality_label(u, int(sizes[u] * 8000 / dur_ms)) if u in sizes
             else lab, u)
            for lab, u in opts]
        # 选中档位的标签也要跟着换（info.quality 是拷贝，不会自己更新）
        for lab, u in info.quality_options:
            if u and u == info.play_url:
                info.quality = lab
                break


def _x_api(tweet_id: str, x_cookie: str = "",
           profile_dir: str = "") -> VideoInfo:
    """GraphQL TweetResultByRestId → syndication 兜底

    顺序很关键：**先 GraphQL 再 syndication**。syndication 的 variants
    只给 type/src，没有 bitrate，拿不到文件大小；GraphQL 的
    video_info.variants 带 bitrate，才能算出"2176kbps · 5.1 MB"。
    syndication 只在 GraphQL 不认（敏感内容/未登录）时兜底。

    `profile_dir` 仅在 queryId 过期需要现场刷新时用到（会开一次无头浏览器）。
    """
    kv = _tw_cookie_kv(x_cookie)
    if kv.get("auth_token"):
        # 1) 有登录态：走 GraphQL（带 bitrate，可估算大小）
        try:
            return _x_graphql(tweet_id, x_cookie, kv, profile_dir)
        except ParseError:
            pass

    # 2) syndication：token + Googlebot UA（敏感内容大多也可取到）
    tombstone = False
    try:
        resp = shared_requests_session("x").get(
            "https://cdn.syndication.twimg.com/tweet-result",
            params={"id": tweet_id, "token": _syndication_token(tweet_id)},
            headers={"User-Agent": "Googlebot"}, timeout=15)
        if resp.ok:
            data = resp.json()
            if data:
                if (data.get("__typename") or "") == "TweetTombstone":
                    # X 对敏感/受限推文只回墓碑，游客令牌同样拿不到（实测
                    # 紧接着必报「未登录无法解析」）→ 别白等这 1 秒多，
                    # 有登录态 profile 时直接交给浏览器渲染
                    tombstone = True
                else:
                    return _tw_syndication_to_info(data, tweet_id)
    except (requests.RequestException, ValueError, KeyError, ParseError):
        pass

    if tombstone and profile_dir:
        raise ParseError("该推文是 X 的敏感/受限内容，需要登录态才能解析"
                         "（请在「设置 → 登录 X(Twitter)」完成登录后重试）")

    # 3) 无登录态（或 syndication 也没结果）：GraphQL + 游客令牌
    return _x_graphql(tweet_id, x_cookie, kv, profile_dir)


_TW_GUEST_CACHE = {"token": "", "ts": 0.0}
_TW_GUEST_LOCK = threading.Lock()


def _x_guest_token(headers: dict, force: bool = False) -> str:
    """进程内缓存 X guest_token（15 分钟 TTL），免去每次未登录请求都 POST activate.json。"""
    now = time.time()
    if not force:
        with _TW_GUEST_LOCK:
            if _TW_GUEST_CACHE["token"] and now - _TW_GUEST_CACHE["ts"] < 900:
                return _TW_GUEST_CACHE["token"]
    try:
        g = shared_requests_session("x").post(
            "https://api.x.com/1.1/guest/activate.json",
            headers=headers, timeout=15)
        if g.ok:
            tok = g.json().get("guest_token", "") or ""
            if tok:
                with _TW_GUEST_LOCK:
                    _TW_GUEST_CACHE["token"] = tok
                    _TW_GUEST_CACHE["ts"] = now
                return tok
    except requests.RequestException:
        pass
    return ""


def _x_graphql(tweet_id: str, x_cookie: str, kv: dict,
               profile_dir: str = "") -> VideoInfo:
    """调 GraphQL TweetResultByRestId；queryId 过期时自动刷新一次"""
    headers = {
        "User-Agent": PC_UA,
        "authorization": f"Bearer {TW_BEARER}",
        "content-type": "application/json",
    }
    is_guest = not bool(kv.get("auth_token"))
    if not is_guest:
        headers["Cookie"] = clean_cookie(x_cookie)
        headers["x-csrf-token"] = kv.get("ct0", "")
    else:
        gt = _x_guest_token(headers)
        if gt:
            headers["x-guest-token"] = gt

    query = json.loads(json.dumps(_GQL_QUERY))
    query["variables"]["tweetId"] = tweet_id

    sess = shared_requests_session("x")

    def _call(endpoint: str):
        return sess.get(
            endpoint,
            params={"variables": json.dumps(query["variables"], separators=(",", ":")),
                    "features": json.dumps(query["features"], separators=(",", ":")),
                    "fieldToggles": json.dumps(query["fieldToggles"], separators=(",", ":"))},
            headers=headers, timeout=20)

    try:
        resp = _call(_gql_endpoint())
        if is_guest and resp.status_code in (401, 403):
            gt = _x_guest_token(headers, force=True)
            if gt:
                headers["x-guest-token"] = gt
                resp = _call(_gql_endpoint())
    except requests.RequestException as e:
        raise ParseError(f"X 接口请求失败：{e}")

    # queryId 过期时 X 返回 400/404：
    # 1) 先用静态候选表逐个试（纯 HTTP，毫秒级）
    # 2) 都无效再开浏览器现场抓一个（慢，但一定能拿到最新值）
    if resp.status_code in (400, 404):
        known = _gql_endpoint().rstrip("/").rsplit("/", 2)[-2]
        for cand in TW_GQL_QUERY_IDS:
            if cand == known:
                continue
            try:
                retry = _call(_gql_endpoint(cand))
            except requests.RequestException:
                continue
            if retry.ok:
                _GQL_QUERY_ID_CACHE["id"] = cand
                resp = retry
                break
    if resp.status_code in (400, 404):
        fresh = _refresh_gql_query_id(profile_dir, tweet_id)
        if fresh:
            try:
                retry = _call(_gql_endpoint(fresh))
                if retry.ok:
                    resp = retry
            except requests.RequestException:
                pass

    if not resp.ok:
        if not kv.get("auth_token"):
            raise ParseError("该推文可能是敏感/受限内容，未登录无法解析。"
                             "请在「设置 → 登录 X(Twitter)」中登录后重试")
        raise ParseError(f"X 接口返回 HTTP {resp.status_code}，"
                         "Cookie 可能已过期，请更新后重试")

    try:
        result = (resp.json().get("data") or {}).get("tweetResult", {}).get("result") or {}
    except ValueError:
        raise ParseError("X 接口返回异常，无法解析")

    typename = result.get("__typename", "")
    if typename == "TweetWithVisibilityResults":
        result = result.get("tweet") or {}
    elif typename == "TweetUnavailable":
        reason = result.get("reason") or ""
        if "Nsfw" in reason:
            raise ParseError("敏感内容需要登录查看，请检查「设置」中的 X Cookie 是否有效")
        raise ParseError(f"推文不可用：{reason or '未知原因'}")

    status = result.get("legacy") or {}
    user_legacy = (((result.get("core") or {}).get("user_results") or {})
                   .get("result") or {}).get("legacy") or {}
    if not status:
        if not kv.get("auth_token"):
            raise ParseError("该推文可能是敏感/受限内容，未登录无法解析。"
                             "请在「设置 → 登录 X(Twitter)」中登录后重试")
        raise ParseError("X 接口未能解析该推文（Cookie 可能已过期，请更新后重试）")
    return _tw_build_info(status, user_legacy, tweet_id)


def _x_browser(tweet_id: str, profile_dir: str) -> VideoInfo:
    """用浏览器渲染 X 推文页（复用持久化登录态），拦截真实视频地址"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise ParseError(f"Playwright 未安装：{e}")

    with sync_playwright() as pw:
        dur_box = {"ms": 0}      # 供下方估算文件大小使用，需在 try 外初始化
        try:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=profile_dir, channel="msedge", headless=True,
                user_agent=PC_UA, locale="zh-CN",
                viewport={"width": 1280, "height": 900})
        except Exception as e:  # noqa: BLE001
            raise ParseError(f"无法启动本机 Edge 浏览器：{e}")
        try:
            captured = []
            json_variants = []

            def on_response(resp):
                try:
                    ct = resp.headers.get("content-type", "")
                    if "video/mp4" in ct or "video/quicktime" in ct:
                        size = 0
                        try:
                            size = int(resp.headers.get("content-length") or 0)
                        except (TypeError, ValueError):
                            size = 0
                        captured.append((resp.url, size))
                        return
                    if "json" in ct and ("x.com" in resp.url or "twitter.com" in resp.url):
                        data = resp.json()
                        vinfo = _find_key_recursive(data, "video_info")
                        if isinstance(vinfo, dict):
                            variants = vinfo.get("variants") or []
                            if isinstance(variants, list):
                                json_variants.extend(variants)
                            try:
                                dm = int(vinfo.get("duration_millis") or 0)
                            except (TypeError, ValueError):
                                dm = 0
                            if dm:
                                dur_box["ms"] = dm
                except Exception:  # noqa: BLE001
                    pass

            from ..browser_bridge import block_heavy_resources
            page = context.new_page()
            block_heavy_resources(page, ("image", "font", "stylesheet"))
            page.on("response", on_response)
            page.goto(f"https://x.com/i/status/{tweet_id}",
                      wait_until="domcontentloaded", timeout=45000)
            # 视频信息随页面响应就到（通常 1~2s），轮询到即走，别固定死等 5s；
            # 命中后留 400ms 让 og / <video> 也渲染出来，兜底路径才不至于落空
            waited = 0
            while waited < 6000 and not (captured or json_variants):
                try:
                    page.wait_for_timeout(200)
                except Exception:  # noqa: BLE001
                    break
                waited += 200
            if captured or json_variants:
                page.wait_for_timeout(400)

            og = page.evaluate(
                "() => document.querySelector('meta[property=\"og:video\"]')?.content || ''")
            video_src = page.evaluate(
                "() => { const v = document.querySelector('video'); return v ? (v.currentSrc || v.src || '') : ''; }")
            title = page.evaluate(
                "() => document.querySelector('meta[property=\"og:title\"]')?.content || ''")
            desc = page.evaluate(
                "() => document.querySelector('meta[property=\"og:description\"]')?.content || ''")
            cover = page.evaluate(
                "() => document.querySelector('meta[property=\"og:image\"]')?.content || ''")
            # og:title 会带上"(2) X 上的 作者："这类浏览器标题前缀（数字是
            # 未读通知数，会变），不能直接当作品标题。这里单独抓推文正文。
            tweet_text = page.evaluate("""() => {
                const el = document.querySelector(
                    '[data-testid="tweetText"]');
                return el ? el.innerText : '';
            }""")

            # 优先用接口 JSON 里的完整渐进式 mp4 档位（长视频的分片会被排除）
            options, seen, x_sizes = [], set(), {}
            for v in json_variants:
                ct = (v.get("content_type") or v.get("type") or "")
                if not ct.startswith("video/mp4"):
                    continue          # 排除 m3u8 播放列表
                url = (v.get("url") or v.get("src") or "").replace("\\u0026", "&")
                if not url or "/m3u8" in url or url in seen:
                    continue
                seen.add(url)
                rate = int(v.get("bitrate") or 0)
                options.append((rate, _x_quality_label(url, rate), url))
                # X 无字节数，用码率 × 时长估算
                if rate > 0 and dur_box["ms"] > 0:
                    x_sizes[url] = int(rate * (dur_box["ms"] / 1000.0) / 8.0)
            options.sort(key=lambda x: x[0], reverse=True)
            quality_options = [(lab, url) for _, lab, url in options]

            video_url = quality_options[0][1] if quality_options else ""
            if not video_url and captured:
                # 兜底：排除带分片偏移（/9000/12000/ 这类）的 URL
                full = [u for u, _ in captured if not re.search(r'/\d{3,}/\d{3,}/', u)]
                pool = full or [u for u, _ in captured]
                best = max(((u for u in pool)), key=lambda u: next(
                    (s for u2, s in captured if u2 == u), 0))
                video_url = best
            if not video_url and og.startswith("http"):
                video_url = og
            if not video_url and video_src.startswith("http"):
                video_url = video_src
        finally:
            context.close()

    if not video_url:
        raise ParseError("未能获取该推文的视频地址。若为敏感内容，"
                         "请在「设置 → 登录 X(Twitter)」登录后重试")

    info = VideoInfo()
    info.source = "x"
    info.aweme_id = tweet_id
    # 正文优先（og:title 带"(N) X 上的 作者："前缀，N 是未读通知数会变）
    clean_title = _x_clean_og_title(tweet_text or desc or title)
    info.title = clean_title.strip()
    info.cover_url = cover
    info.play_url = video_url
    info.play_url_candidates = [u for _, u in quality_options] or [video_url]
    if quality_options:
        info.quality_options = quality_options
        info.quality_sizes = {u: s for u, s in x_sizes.items()
                              if u in {x for _l, x in quality_options}}
    else:
        info.quality_options = [("最高画质（原始）", video_url)]
    info.quality = info.quality_options[0][0]
    if dur_box["ms"]:
        info.duration = dur_box["ms"]
    return info


def _tw_syndication_to_info(data: dict, tweet_id: str) -> VideoInfo:
    """从 syndication JSON 构建 VideoInfo"""
    info = VideoInfo()
    info.source = "x"
    info.aweme_id = tweet_id
    info.title = _x_clean_og_title(data.get("text") or "")
    user = data.get("user") or {}
    info.author = user.get("name") or user.get("screen_name") or ""
    info.digg_count = int(data.get("favorite_count", 0) or 0)
    info.comment_count = int(data.get("conversation_count", 0) or 0)

    video = data.get("video") or {}
    variants = video.get("variants") or []
    if variants:
        # 时长要先取出来：下面按 码率 × 时长 / 8 估算文件大小
        info.duration = int(video.get("durationMs", 0) or 0)
        mp4s = []
        for v in variants:
            if (v.get("type") or "").startswith("video/mp4") and v.get("src"):
                mp4s.append((int(v.get("bitrate") or 0),
                             v["src"].replace("\\u0026", "&")))
        if mp4s:
            mp4s.sort(key=lambda x: x[0], reverse=True)
            options, seen, sizes = [], set(), {}
            for rate, url in mp4s:
                if url not in seen:
                    seen.add(url)
                    options.append((_x_quality_label(url, rate), url))
                    # X 不返回字节数，用码率 × 时长估算
                    if rate > 0 and info.duration > 0:
                        sizes[url] = int(rate * (info.duration / 1000.0) / 8.0)
            info.quality_options = options
            info.quality_sizes = sizes
            info.play_url_candidates = [u for _, u in options]
            info.play_url = options[0][1]
            info.quality = options[0][0]

    photos = data.get("photos") or []
    if photos:
        info.image_urls = [p["url"] for p in photos if p.get("url")]
    media = data.get("mediaDetails") or []
    if media and not info.cover_url:
        info.cover_url = media[0].get("media_url_https", "")
    if not info.cover_url and photos:
        info.cover_url = photos[0].get("url", "")

    if not info.play_url and not info.image_urls:
        raise ParseError("该推文不包含可下载的视频或图片")
    if info.image_urls and not info.play_url:
        info.is_image = True
    return info
