# -*- coding: utf-8 -*-
"""Instagram 单作品解析（纯 HTTP 三层快路径 + 浏览器兜底）。"""
import json
import re
import threading
from typing import Optional

import requests

from ..http_pool import (
    cffi_proxies,
    read_profile_cookie_cache,
    shared_cffi_session,
    shared_requests_session,
)
from ..models import VideoInfo, clean_cookie
from ..parser import PC_UA, ParseError


# --------------------------------------------------------------------- #
# Instagram
# --------------------------------------------------------------------- #
def parse_instagram(url: str, cookie: str = "",
                    profile_dir: str = "") -> VideoInfo:
    """解析 Instagram 帖子（reel/p）：纯 HTTP 三层快路径为主，浏览器渲染兜底。"""
    m = re.search(r'/(?:reel|reels|p)/([A-Za-z0-9_-]+)', url)
    if not m:
        raise ParseError("无法从链接中识别 Instagram 帖子 ID")
    shortcode = m.group(1)
    # `/reel(s)/` 是视频帖的专用路径；`/p/` 才是「图或视频都可能有」。
    # 这个区别在下面两条兜底链路上都要用：reel 永远不该被当成图集。
    is_reel = bool(re.search(r'/reels?/', url))

    # 1) 纯 HTTP 快路径（Mobile info API → Embed HTML → Web GraphQL），0 浏览器 ~0.5s
    fast = _instagram_http_fast(shortcode, cookie, profile_dir, is_reel)
    if fast is not None:
        return fast

    # 2) HTTP 直连：后台线程跑（公开帖子的 og 标签/内嵌 JSON 仍可取到）
    got = {}

    def _http_try():
        try:
            got["info"] = _instagram_http(shortcode, cookie, is_reel)
        except Exception:  # noqa: BLE001
            pass

    probe = threading.Thread(target=_http_try, daemon=True)
    probe.start()

    # 3) 浏览器渲染兜底（登录态 / 受限内容）
    try:
        return _instagram_browser(shortcode, cookie, profile_dir or None,
                                  is_reel)
    except ParseError:
        probe.join(timeout=3)
        if got.get("info"):
            return got["info"]
        raise


def _ig_http_cffi_get(url: str, headers: dict, timeout: int = 12):
    sess = shared_cffi_session("instagram", impersonate="chrome")
    proxies = cffi_proxies(url)
    kwargs = {"headers": headers, "timeout": timeout}
    if proxies:
        kwargs["proxies"] = proxies
    return sess.get(url, **kwargs)


def _ig_http_cffi_post(url: str, headers: dict, data: dict, timeout: int = 12):
    sess = shared_cffi_session("instagram", impersonate="chrome")
    proxies = cffi_proxies(url)
    kwargs = {"headers": headers, "data": data, "timeout": timeout}
    if proxies:
        kwargs["proxies"] = proxies
    return sess.post(url, **kwargs)


def _ig_node_from_gql(media: dict, shortcode: str) -> dict:
    """把 Instagram GraphQL / Embed 的 shortcode_media 结构转成统一 node。"""
    if not isinstance(media, dict) or not media:
        return {}
    code = media.get("shortcode") or media.get("code") or ""
    if code and code != shortcode:
        return {}
    out: dict = {}
    vurl = (media.get("video_url") or "").replace("\\u0026", "&").replace("&amp;", "&")
    if vurl:
        dims = media.get("dimensions") or {}
        w = int(dims.get("width") or 0)
        h = int(dims.get("height") or 0)
        out["quality_options"] = [(f"{w}×{h}" if w and h else "最高画质", vurl)]
    sidecar = ((media.get("edge_sidecar_to_children") or {}).get("edges")) or []
    if sidecar:
        imgs = []
        for edge in sidecar:
            node = (edge or {}).get("node") or {}
            if not out.get("quality_options") and node.get("is_video") and node.get("video_url"):
                nv = str(node["video_url"]).replace("\\u0026", "&").replace("&amp;", "&")
                out["quality_options"] = [("视频", nv)]
            durl = (node.get("display_url") or "").replace("\\u0026", "&").replace("&amp;", "&")
            if durl:
                imgs.append(durl)
        if imgs:
            out["image_urls"] = imgs
    cover = (media.get("display_url") or media.get("thumbnail_src") or "").replace("\\u0026", "&").replace("&amp;", "&")
    if cover:
        out["cover"] = cover
    if media.get("video_duration"):
        out["duration"] = media["video_duration"]
    owner = media.get("owner") or {}
    if owner.get("username"):
        out["author"] = owner["username"]
    edges = ((media.get("edge_media_to_caption") or {}).get("edges")) or []
    if edges and isinstance(edges[0], dict):
        out["title"] = ((edges[0].get("node") or {}).get("text")) or ""
    out["like"] = ((media.get("edge_media_preview_like") or {}).get("count")) or 0
    out["comment"] = ((media.get("edge_media_to_comment") or {}).get("count")) or 0
    return out


def _ig_info_from_node(node: dict, shortcode: str,
                       is_reel: bool) -> Optional[VideoInfo]:
    """把统一的 IG node 转成 VideoInfo；不满足帖子类型（如 reel 只有图）时返回 None。"""
    if not node:
        return None
    opts = node.get("quality_options") or []
    imgs = node.get("image_urls") or []
    if is_reel and not opts:
        return None
    if not opts and not imgs:
        return None

    info = VideoInfo()
    info.source = "instagram"
    info.aweme_id = shortcode
    info.title = node.get("title") or ""
    info.author = node.get("author") or ""
    info.digg_count = int(node.get("like") or 0)
    info.comment_count = int(node.get("comment") or 0)
    info.cover_url = node.get("cover") or (imgs[0] if imgs else "")
    if node.get("duration"):
        info.duration = int(float(node["duration"]) * 1000)

    if opts:
        info.play_url = opts[0][1]
        info.play_url_candidates = [u for _, u in opts]
        info.quality_options = opts
        info.quality_sizes = node.get("quality_sizes") or {}
        info.quality = opts[0][0]
        return info

    info.image_urls = imgs
    info.is_image = True
    return info


def _instagram_http_fast(shortcode: str, cookie: str, profile_dir: str,
                         is_reel: bool) -> Optional[VideoInfo]:
    """参考 cobalt 三层纯 HTTP 策略（Mobile API → Embed HTML → Web GraphQL）。"""
    eff_cookie = clean_cookie(cookie or "")
    if not eff_cookie and profile_dir:
        eff_cookie = clean_cookie(read_profile_cookie_cache(profile_dir, "instagram.com"))

    # 1) Mobile API（带 sessionid Cookie 时稳定拿到高清版本与 Carousel）
    if "sessionid=" in eff_cookie:
        media_id = _ig_media_id_from_shortcode(shortcode)
        if media_id:
            try:
                m_headers = {
                    "User-Agent": "Instagram 275.0.0.27.98 Android (33/13; 280dpi; 720x1423; Xiaomi; Redmi 7; onclite; qcom; en_US; 458229237)",
                    "x-ig-app-id": "936619743392459",
                    "Cookie": eff_cookie,
                }
                resp = _ig_http_cffi_get(
                    f"https://i.instagram.com/api/v1/media/{media_id}/info/",
                    m_headers, timeout=10,
                )
                if resp.status_code == 200:
                    items = (resp.json() or {}).get("items") or []
                    if items:
                        node = _ig_node_from_item(items[0], shortcode)
                        info = _ig_info_from_node(node, shortcode, is_reel)
                        if info is not None:
                            return info
            except Exception:  # noqa: BLE001
                pass

    base_headers = {
        "User-Agent": PC_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if eff_cookie:
        base_headers["Cookie"] = eff_cookie

    # 2) Embed HTML（公开帖免登录即可直出 contextJSON）
    try:
        resp = _ig_http_cffi_get(
            f"https://www.instagram.com/p/{shortcode}/embed/captioned/",
            base_headers, timeout=10,
        )
        if resp.status_code == 200 and resp.text:
            mm = re.search(r'"init",\[\],\[(.*?)\]\],', resp.text)
            if mm:
                embed_data = json.loads(mm.group(1))
                ctx_raw = embed_data.get("contextJSON") if isinstance(embed_data, dict) else None
                if ctx_raw:
                    ctx_obj = json.loads(ctx_raw)
                    gql_media = ((ctx_obj.get("gql_data") or {}).get("shortcode_media")
                                 or (ctx_obj.get("gql_data") or {}).get("xdt_shortcode_media")
                                 or {})
                    node = _ig_node_from_gql(gql_media, shortcode)
                    info = _ig_info_from_node(node, shortcode, is_reel)
                    if info is not None:
                        return info
    except Exception:  # noqa: BLE001
        pass

    # 3) Web GraphQL（从公开页提取 LSD / csrftoken 后直调 PolarisPostActionLoadPostQueryQuery）
    try:
        page_resp = _ig_http_cffi_get(
            f"https://www.instagram.com/p/{shortcode}/",
            base_headers, timeout=10,
        )
        html = page_resp.text if page_resp.status_code == 200 else ""
        if html and "accounts/login" not in str(page_resp.url):
            lsd_m = re.search(r'\["LSD",.*?\{"token":"([^"]+)"\}', html)
            csrf_m = re.search(r'"csrf_token":"([^"]+)"', html)
            lsd = lsd_m.group(1) if lsd_m else ""
            csrf = csrf_m.group(1) if csrf_m else ""
            gql_headers = {
                "User-Agent": PC_UA,
                "x-ig-app-id": "936619743392459",
                "x-asbd-id": "129477",
                "X-FB-Friendly-Name": "PolarisPostActionLoadPostQueryQuery",
                "content-type": "application/x-www-form-urlencoded",
                "Referer": f"https://www.instagram.com/p/{shortcode}/",
            }
            if lsd:
                gql_headers["X-FB-LSD"] = lsd
            if csrf:
                gql_headers["X-CSRFToken"] = csrf
            if eff_cookie:
                gql_headers["Cookie"] = eff_cookie
            elif csrf:
                gql_headers["Cookie"] = f"csrftoken={csrf}"
            gql_resp = _ig_http_cffi_post(
                "https://www.instagram.com/graphql/query",
                gql_headers,
                data={
                    "lsd": lsd,
                    "fb_api_caller_class": "RelayModern",
                    "fb_api_req_friendly_name": "PolarisPostActionLoadPostQueryQuery",
                    "variables": json.dumps({
                        "shortcode": shortcode,
                        "fetch_tagged_user_count": None,
                        "hoisted_comment_id": None,
                        "hoisted_reply_id": None,
                    }, separators=(",", ":")),
                    "server_timestamps": "true",
                    "doc_id": "8845758582119845",
                },
                timeout=10,
            )
            if gql_resp.status_code == 200:
                gdata = (gql_resp.json() or {}).get("data") or {}
                media = gdata.get("xdt_shortcode_media") or gdata.get("shortcode_media") or {}
                node = _ig_node_from_gql(media, shortcode)
                info = _ig_info_from_node(node, shortcode, is_reel)
                if info is not None:
                    return info
    except Exception:  # noqa: BLE001
        pass

    return None


def _instagram_http(shortcode: str, cookie: str = "",
                    is_reel: bool = False) -> VideoInfo:
    # Cookie 先统一清洗（非 Latin-1 字符 percent-encode），再拆键值对，
    # 保证 Cookie header 与 x-csrftoken 用的是同一份值（见 models.clean_cookie）
    cookie = clean_cookie(cookie)
    cookie_kv = {}
    for pair in cookie.split(";"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            cookie_kv[k.strip()] = v.strip()

    session = shared_requests_session("instagram")
    headers = {
        "User-Agent": PC_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.instagram.com/",
    }
    if cookie:
        headers["Cookie"] = cookie
    if cookie_kv.get("csrftoken"):
        headers["x-csrftoken"] = cookie_kv["csrftoken"]

    page_url = f"https://www.instagram.com/p/{shortcode}/"
    try:
        resp = session.get(page_url, headers=headers, timeout=20, allow_redirects=True)
    except requests.RequestException as e:
        raise ParseError(f"Instagram 请求失败：{e}")
    html = resp.text

    if "accounts/login" in resp.url:
        raise ParseError("HTTP 直连被登录墙拦截")

    info = VideoInfo()
    info.source = "instagram"
    info.aweme_id = shortcode

    mv = re.search(r'<meta property="og:video" content="([^"]+)"', html)
    mt = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    mc = re.search(r'<meta property="og:image" content="([^"]+)"', html)
    if mt:
        title = mt.group(1)
        pm = re.search(r'^(.+?) on Instagram: ?(.*)$', title)
        if pm:
            info.author = pm.group(1).strip()
            info.title = pm.group(2).strip().strip('"')
        else:
            info.title = title
    if mc:
        info.cover_url = mc.group(1).replace("&amp;", "&")
    if mv:
        info.play_url = mv.group(1).replace("&amp;", "&")
        info.play_url_candidates = [info.play_url]
        info.quality_options = [("最高画质（原始）", info.play_url)]
        info.quality = "最高画质（原始）"
        return info

    m2 = re.search(r'"video_versions"\s*:\s*(\[.*?\])', html)
    if m2:
        try:
            versions = json.loads(m2.group(1))
            versions.sort(key=lambda v: int(v.get("width", 0) or 0), reverse=True)
            options, seen = [], set()
            for v in versions:
                url = (v.get("url") or "").replace("&amp;", "&")
                if not url or url in seen:
                    continue
                seen.add(url)
                w = int(v.get("width") or 0)
                h = int(v.get("height") or 0)
                label = f"{w}×{h}" if w and h else (f"{w}p" if w else "视频")
                options.append((label, url))
            if options:
                info.quality_options = options
                info.play_url_candidates = [u for _, u in options]
                info.play_url = options[0][1]
                info.quality = options[0][0]
                return info
        except (ValueError, TypeError, KeyError):
            pass

    disp = re.findall(r'"display_url"\s*:\s*"([^"]+)"', html)
    if disp:
        # `/reel/` 链接按定义就是视频：HTML 里的 display_url 是它的封面帧，
        # 不能因为「没解析出 video_versions」就把它当图集（页面是 JS 壳时
        # display_url 常在、video_versions 却还没渲染出来）。宁可返回失败，
        # 让上层走浏览器链路，也不要给出「reel = 一张图」这种错结论。
        if is_reel:
            raise ParseError("HTTP 直连只拿到 reel 的封面帧，未取到视频地址")
        info.image_urls = [d.replace("\\u0026", "&").replace("&amp;", "&")
                           for d in disp]
        info.is_image = True
        if not info.cover_url and info.image_urls:
            info.cover_url = info.image_urls[0]
        return info

    raise ParseError("HTTP 直连未能提取到内容")


def _instagram_browser(shortcode: str, cookie: str,
                       profile_dir, is_reel: bool = False) -> VideoInfo:
    """用浏览器渲染 Instagram 页面（现代 IG 是 JS 壳，requests 拿不到数据）

    有 profile_dir 时用持久化上下文（复用网页登录态），否则注入 cookie。

    ⚠️ 取数必须「精确对应本条帖子」：
    IG 的推荐流会在同一批响应里带回其他帖子的数据，早期版本用
    「递归找第一个 video_versions」会拿到**别人的视频**（封面却是对的，
    因为封面走 og:image）。现在改为按 media_id 走 info 接口，
    并校验返回的 code 与目标 shortcode 一致。

    取数顺序（按代价从低到高，全部走页面内 fetch）：
      1) goto 完立刻用「shortcode 本地解码出的 media_id」调 info 接口
         （实测 0.7s，且不必等页面 JS 渲染出数据）
      2) 拿不到才读页面 HTML 轮询等渲染，再依次尝试
         HTML 里的 media_id → 内嵌 JSON → og 兜底
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise ParseError(f"Playwright 未安装：{e}")

    cookies = []
    for pair in (cookie or "").split(";"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            if k.strip():
                cookies.append({"name": k.strip(), "value": v.strip(),
                                "domain": ".instagram.com", "path": "/"})

    with sync_playwright() as pw:
        try:
            if profile_dir:
                context = pw.chromium.launch_persistent_context(
                    user_data_dir=profile_dir, channel="msedge", headless=True,
                    user_agent=PC_UA, locale="en-US",
                    viewport={"width": 1280, "height": 900})
                browser = None
            else:
                browser = pw.chromium.launch(channel="msedge", headless=True)
                context = browser.new_context(user_agent=PC_UA, locale="en-US",
                                              viewport={"width": 1280, "height": 900})
                if cookies:
                    try:
                        context.add_cookies(cookies)
                    except Exception:  # noqa: BLE001
                        pass
        except Exception as e:  # noqa: BLE001
            raise ParseError(f"无法启动本机 Edge 浏览器：{e}")
        try:
            from ..browser_bridge import block_heavy_resources, cache_context_cookies
            page = context.new_page()
            block_heavy_resources(page)
            page.goto(f"https://www.instagram.com/p/{shortcode}/",
                      wait_until="domcontentloaded", timeout=45000)
            # 账号被 IG 限流 / 要求安全验证时，**所有**页面都会被 302 到
            # /accounts/suspended/（连公开帖也一样）。此时页面里没有任何帖子
            # 数据，若只报「页面未能加载内容，请重新登录」会把人带偏 ——
            # 登录态其实是好的，重新登录也没用（2026-10-14 实测：用户 sessionid
            # 长度 78 有效，但全站被跳 suspended，页面要求输入短信验证码）。
            if "accounts/suspended" in (page.url or ""):
                raise ParseError(
                    "Instagram 要求先完成安全验证：账号被临时限制，所有页面都会跳到"
                    "「账号已暂停」页（页面会提示输入短信验证码）。"
                    "请用浏览器登录 instagram.com 按提示完成验证，之后即可正常解析。"
                    "（这不是登录失效，重新登录无效）")
            # ① 首选：/api/v1/media/{media_id}/info/（数据最全且有校验字段）
            #    media_id 可由 shortcode 本地解码（IG 的 shortcode 就是
            #    media_id 的 base64url 变体），所以**不必**等页面 JS 把数据
            #    渲染出来再取 id —— 省掉原先固定 5s 的干等。
            media_id = _ig_media_id_from_shortcode(shortcode)
            node = _ig_media_info(page, media_id, shortcode) if media_id else {}

            # ② 接口拿不到（未登录 / 受限内容）才读页面：
            #    HTML 里的 media_id → 内嵌 JSON → og 兜底
            cover = title = ""
            if not node:
                html = _ig_page_html(page)
                cover = _ig_meta(page, "og:image")
                title = _ig_meta(page, "og:title")
                mm = re.search(r'"media_id"\s*:\s*"(\d+)"', html or "")
                if mm and mm.group(1) != media_id:
                    node = _ig_media_info(page, mm.group(1), shortcode)
                if not node:
                    node = _ig_html_node(html, shortcode)
            elif not node.get("cover"):
                # 接口没给封面时用 og:image 补（meta 是服务端直出，代价极小）
                cover = _ig_meta(page, "og:image")

            # IG 接口不给文件大小，批量 HEAD 探 Content-Length（借页面登录态）
            opts = node.get("quality_options") or []
            if opts:
                sizes = _probe_sizes(page, [u for _lab, u in opts])
                if sizes:
                    node["quality_sizes"] = sizes
            if node:
                cache_context_cookies(context, profile_dir, "instagram.com")
        finally:
            context.close()
            if browser:
                browser.close()

    if not node and not cover:
        raise ParseError("Instagram 页面未能加载内容。若为私密账号或登录失效，"
                         "请点击「登录 Instagram」重新登录后重试")

    info = VideoInfo()
    info.source = "instagram"
    info.aweme_id = shortcode
    info.title = node.get("title") or ""
    info.author = node.get("author") or ""
    info.digg_count = node.get("like") or 0
    info.comment_count = node.get("comment") or 0
    info.cover_url = node.get("cover") or cover
    if node.get("duration"):
        info.duration = int(node["duration"]) * 1000

    if not info.author or not info.title:
        pm = re.search(r'^(.+?) on Instagram: ?(.*)$', title or "", re.S)
        if pm:
            info.author = info.author or pm.group(1).strip()
            info.title = info.title or pm.group(2).strip().strip('"')

    quality_options = node.get("quality_options") or []
    video_url = quality_options[0][1] if quality_options else ""
    if video_url:
        info.play_url = video_url
        info.play_url_candidates = [u for _, u in quality_options]
        info.quality_options = quality_options
        info.quality_sizes = node.get("quality_sizes") or {}
        info.quality = quality_options[0][0]
        return info

    # 到这里说明没拿到视频直链。必须先分清两种情况，否则会把
    # 「登录墙下的残缺页」当成正常图集（用户看到的就是「reel 解析成图片、
    # 点赞 0」——2026-10-14 实测：年龄限制帖只回登录墙，页面里没有
    # video_versions，最后被 og:image 兜底成一张图）。
    #   · node 有数据（含 cover/image_urls）→ 真的是图集，正常返回
    #   · node 为空、只有 og:image       → 连帖子数据都没取到，是登录态/
    #     权限问题，硬报图集只会误导；这里直接说清原因
    if not node:
        raise ParseError(
            "Instagram 只返回了封面，没取到"
            + ("视频" if is_reel else "帖子")
            + "数据 —— 本条多为年龄限制 / 受限内容，需要登录态。"
              "请在「设置 → 账号与登录 → Instagram」填写 Cookie 或重新登录后重试")

    # 图集：用接口给出的多图候选（精确对应本条）。
    # 单图帖子接口只在 cover 里给一张，用它兜底；og:image 再兜底。
    single = node.get("cover") or cover
    imgs = node.get("image_urls") or ([single] if single else [])
    info.image_urls = imgs
    info.is_image = True
    return info


IG_APP_ID = "936619743392459"

# 页面内发请求：媒体 info 接口 + 文件大小探测。
# ⚠️ 必须走页面内 fetch，不能用 Playwright 的 page.request ——
# 实测本机环境下 page.request 的每一次调用都会一路超时
# （info 接口 20~30s、CDN HEAD 每个 8s 后才 TimeoutError），
# 一次解析光这部分就白等 20~40s；而页面内 fetch 同一条 URL
# 0.1s 内就返回，且自动带登录 Cookie / 站点指纹，CDN 不会 403。
_IG_INFO_JS = r"""async (mid) => {
    try {
        const headers = {'x-ig-app-id': '%s'};
        const m = document.cookie.match(/(?:^|; )csrftoken=([^;]*)/);
        if (m) headers['x-csrftoken'] = decodeURIComponent(m[1]);
        const r = await fetch(`/api/v1/media/${mid}/info/`,
                              {headers: headers, credentials: 'include'});
        return await r.text();
    } catch (e) {
        return '';
    }
}""" % IG_APP_ID

_IG_SIZE_JS = r"""async (urls) => {
    const out = {};
    await Promise.all(urls.map(async (u) => {
        let n = 0;
        try {
            const r = await fetch(u, {method: 'HEAD'});
            n = parseInt(r.headers.get('content-length') || '0', 10) || 0;
            if (r.status !== 200 || !n) {
                // 部分 CDN 不支持 HEAD：退回只取 1 字节的 Range 请求读总长
                const r2 = await fetch(u, {headers: {'Range': 'bytes=0-0'}});
                const cr = r2.headers.get('content-range') || '';
                const m = cr.match(/\/(\d+)\s*$/);
                n = m ? parseInt(m[1], 10)
                      : (parseInt(r2.headers.get('content-length') || '0', 10) || 0);
                try { if (r2.body) r2.body.cancel(); } catch (e) {}
            }
        } catch (e) {
            n = 0;
        }
        out[u] = n;
    }));
    return out;
}"""


def _ig_media_id_from_shortcode(shortcode: str) -> str:
    """由 shortcode 本地解码出 media_id

    IG 的 shortcode 就是把 media_id 按 base64url 变体（A-Za-z0-9-_）编码
    后的字符串，因此不需要先抓页面 HTML 再正则找 `"media_id"` ——
    省掉「等页面渲染」这一步。解错也无妨：info 接口返回的 code 与目标
    shortcode 不一致时会被丢弃，自动走页面解析兜底。
    """
    alphabet = ("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                "0123456789-_")
    if not shortcode:
        return ""
    n = 0
    for ch in shortcode:
        idx = alphabet.find(ch)
        if idx < 0:
            return ""
        n = n * 64 + idx
    return str(n)


def _ig_page_html(page, timeout_ms: int = 5000) -> str:
    """轮询等本条帖子的数据渲染进页面，出现即返回（不再固定 sleep）"""
    waited, html = 0, ""
    while True:
        try:
            html = page.content()
        except Exception:  # noqa: BLE001
            return ""
        if any(k in html for k in ('"media_id"', '"video_versions"',
                                   '"display_url"', '"image_versions2"')):
            return html
        if waited >= timeout_ms:
            return html
        try:
            page.wait_for_timeout(250)
        except Exception:  # noqa: BLE001
            return html
        waited += 250


def _ig_meta(page, prop: str) -> str:
    """读页面 og meta（仅接口不可用时兜底用）"""
    try:
        return page.evaluate(
            "p => document.querySelector('meta[property=\"' + p + '\"]')"
            "?.content || ''", prop) or ""
    except Exception:  # noqa: BLE001
        return ""


def _probe_sizes(page, urls) -> dict:
    """借页面登录态批量 HEAD 取 Content-Length（拿不到的不返回）

    用于接口不返回文件大小的平台（如 Instagram）。
    """
    todo = [u for u in dict.fromkeys(urls or []) if u]
    if not todo:
        return {}
    try:
        res = page.evaluate(_IG_SIZE_JS, todo) or {}
    except Exception:  # noqa: BLE001
        return {}
    return {u: int(n) for u, n in res.items() if int(n or 0) > 0}


def _ig_node_from_item(it: dict, shortcode: str) -> dict:
    """把 IG /api/v1/media/{id}/info/ 返回的 item 转成统一 node；code 不一致则返回空。"""
    if not isinstance(it, dict):
        return {}
    code = it.get("code") or ""
    if code and code != shortcode:
        return {}

    out = {}
    vs = it.get("video_versions") or []
    options, seen = [], set()
    for v in sorted(vs, key=lambda x: int(x.get("width") or 0), reverse=True):
        url = (v.get("url") or "").replace("\\u0026", "&").replace("&amp;", "&")
        if not url or url in seen:
            continue
        seen.add(url)
        w = int(v.get("width") or 0)
        h = int(v.get("height") or 0)
        options.append((f"{w}×{h}" if w and h else "视频", url))
    if options:
        out["quality_options"] = options

    cands = ((it.get("image_versions2") or {}).get("candidates")) or []
    if cands:
        out["cover"] = (cands[0].get("url") or "").replace("&amp;", "&")
        carousel = it.get("carousel_media") or []
        if carousel:
            imgs = []
            for c in carousel:
                cc = ((c.get("image_versions2") or {}).get("candidates")) or []
                if cc and cc[0].get("url"):
                    imgs.append(cc[0]["url"])
            if imgs:
                out["image_urls"] = imgs

    out["like"] = it.get("like_count") or 0
    out["comment"] = it.get("comment_count") or 0
    out["duration"] = it.get("video_duration") or 0
    cap = it.get("caption") or {}
    if isinstance(cap, dict) and cap.get("text"):
        out["title"] = cap["text"]
    elif isinstance(cap, str):
        out["title"] = cap
    user = it.get("user") or {}
    if user.get("username"):
        out["author"] = user["username"]
    elif user.get("full_name"):
        out["author"] = user["full_name"]
    return out


def _ig_media_info(page, media_id: str, shortcode: str) -> dict:
    """调 /api/v1/media/{id}/info/ 取本条帖子的精确数据。

    返回统一结构的 dict（空 dict 表示不可用）。
    关键校验：返回的 code 必须等于目标 shortcode，否则丢弃。
    """
    if not media_id:
        return {}
    try:
        text = page.evaluate(_IG_INFO_JS, str(media_id))
        data = json.loads(text) if text else {}
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(data, dict):
        return {}

    items = data.get("items") or []
    if not items:
        return {}
    return _ig_node_from_item(items[0], shortcode)


def _ig_html_node(html: str, shortcode: str) -> dict:
    """从页面 HTML 内嵌 JSON 里取本条帖子的视频/图片（本页数据，非推荐流）。

    HTML 里的 video_versions 只对应当前页面，但仍校验 code 一致才采用。
    """
    out = {}
    if not html:
        return out

    # 校验 HTML 里的 code 确实是目标帖子
    code_ok = bool(re.search(r'"code"\s*:\s*"%s"' % re.escape(shortcode), html))
    if not code_ok:
        return out

    idx = html.find('"video_versions"')
    if idx >= 0:
        start = html.find("[", idx)
        if start > 0:
            depth, end = 0, start
            while end < len(html) and end < start + 40000:
                c = html[end]
                if c == "[":
                    depth += 1
                elif c == "]":
                    depth -= 1
                    if depth == 0:
                        break
                end += 1
            try:
                arr = json.loads(html[start:end + 1])
                options, seen = [], set()
                for v in arr:
                    url = (v.get("url") or "").replace("\\u0026", "&").replace("&amp;", "&")
                    if not url or url in seen:
                        continue
                    seen.add(url)
                    w = int(v.get("width") or 0)
                    h = int(v.get("height") or 0)
                    options.append((f"{w}×{h}" if w and h else "视频", url))
                if options:
                    out["quality_options"] = options
            except (ValueError, TypeError):
                pass

    m = re.search(r'"like_count"\s*:\s*(\d+)', html)
    if m:
        out["like"] = int(m.group(1))
    m = re.search(r'"comment_count"\s*:\s*(\d+)', html)
    if m:
        out["comment"] = int(m.group(1))
    m = re.search(r'"video_duration"\s*:\s*([\d.]+)', html)
    if m:
        out["duration"] = float(m.group(1))
    return out
