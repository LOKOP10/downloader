# -*- coding: utf-8 -*-
"""小红书单作品解析（API 直连签名 + SSR 页面 / Playwright 兜底）。"""
import json
import re
import urllib.parse
from typing import Optional

import requests

from ..applog import get_logger
from ..http_pool import shared_requests_session
from ..models import VideoInfo, clean_cookie
from ..parser import ParseError


# --------------------------------------------------------------------- #
# 小红书（xiaohongshu）
# --------------------------------------------------------------------- #
XHS_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0")
XHS_HOST = "https://www.xiaohongshu.com"


def parse_xiaohongshu(url: str, cookie: str = "",
                      profile_dir: str = "") -> VideoInfo:
    """解析小红书笔记（视频 / 图集）。

    主路径：用 curl_cffi + 本地签名直连 `edith /api/sns/web/v1/feed`
    JSON 接口（vendor 自 Spider_XHS），需登录态（a1 + web_session），
    不依赖浏览器、对代理更稳。

    兜底：仍保留原浏览器渲染（Playwright + SSR noteCard）与 HTTP 直连，
    用于游客态 / 未登录 / 签名接口失效时。

    **重要**：`xsec_token` 只能来自 App「分享 → 复制链接」得到的链接，
    因此本平台请粘贴 App 分享出来的链接（xhslink.com 短链或带
    `xsec_token=` 的长链接）。
    """
    note_id, token = _xhs_ids(url)

    # 1) 签名直连 JSON 接口（需登录态，最快最稳；失败静默回退）
    info = _xhs_api(url, note_id, token, cookie)
    if info and (info.play_url or info.image_urls):
        return info

    # 2) 浏览器渲染兜底（小红书对请求签名敏感，且 SSR 只在浏览器里完整）
    try:
        info = _xhs_browser(url, note_id, token, profile_dir or None)
        if info and (info.play_url or info.image_urls):
            return info
    except ParseError:
        raise
    except Exception:
        pass

    # 3) HTTP 直连兜底（部分公开笔记 SSR 直接可见）
    try:
        return _xhs_http(url, note_id, token, cookie)
    except ParseError:
        raise
    except Exception as e:
        raise ParseError(f"小红书解析失败：{e}")


def _xhs_has_device_cookie(ctx) -> bool:
    """判断当前上下文是否已具备小红书设备 Cookie

    小红书的服务端只有在识别到设备 Cookie（a1 为主，配合 webId /
    web_session）时才下发含 noteCard 的完整 SSR。全新 profile 的首次
    导航必须先去站点主页把这些 Cookie 落盘，否则笔记页只能拿到空壳。
    """
    try:
        names = {c.get("name") for c in ctx.cookies()
                 if "xiaohongshu" in (c.get("domain") or "")}
    except Exception:  # noqa: BLE001
        return False
    return "a1" in names


def _xhs_ids(url: str) -> tuple:
    """从各种小红书链接形态中取 note_id 与 xsec_token"""
    note_id = ""
    m = re.search(r'/(?:explore|discovery/item|item)/([0-9a-fA-F]{24})', url)
    if m:
        note_id = m.group(1)
    if not note_id:
        # 只在 path 里捞 24 位 hex，避免 login?redirectPath=... 把
        # URL 编码碎片（如 2F6a92a3...）当成 note_id。
        path = urllib.parse.urlparse(url).path or ""
        m = re.search(r'([0-9a-fA-F]{24})', path)
        if m:
            note_id = m.group(1)
    token = ""
    q = urllib.parse.urlparse(url).query
    if q:
        token = (urllib.parse.parse_qs(q).get("xsec_token") or [""])[0]
    if not token:
        frag = urllib.parse.urlparse(url).fragment
        if frag:
            token = (urllib.parse.parse_qs(frag).get("xsec_token") or [""])[0]
    return note_id, token


def _xhs_api(url: str, note_id: str, token: str,
             cookie: str) -> Optional[VideoInfo]:
    """签名直连 edith /api/sns/web/v1/feed 拿笔记 JSON（需登录态）。

    仅当 Cookie 同时含 a1 + web_session 时可用；否则返回 None 交给
    _xhs_browser 兜底。任何异常都不向上抛，静默回退，避免打断解析。
    """
    cookie = clean_cookie(cookie or "")
    if "a1=" not in cookie or "web_session=" not in cookie:
        return None
    target = url
    if not note_id or not token:
        resolved = _xhs_resolve_short(url, cookie)
        r_id, r_tok = _xhs_ids(resolved)
        if r_id:
            note_id = note_id or r_id
        if r_tok:
            token = token or r_tok
            target = resolved
    if not note_id or not token:
        return None
    # 落地 URL 必须带 xsec_token，否则 feed 接口会 300031
    if "xsec_token" not in target:
        target = (f"{XHS_HOST}/explore/{note_id}"
                  f"?xsec_token={urllib.parse.quote(token)}"
                  f"&xsec_source=pc_share")
    try:
        from ..xhsapi import XHS_Apis, XHSPcAuth

        auth = XHSPcAuth.from_cookie(cookie)
    except Exception:  # noqa: BLE001 - 登录态无效 / 签名初始化失败
        return None
    try:
        success, _msg, res = XHS_Apis(auth).get_note_info(target)
        if not success:
            return None
        items = ((res or {}).get("data") or {}).get("items") or []
        if not items:
            return None
        card = items[0].get("note_card") or items[0].get("noteCard")
        info = _xhs_api_card_to_info(card, note_id)
        return info if info and (info.play_url or info.image_urls) else None
    except Exception:  # noqa: BLE001
        return None
    finally:
        try:
            auth.close()
        except Exception:  # noqa: BLE001
            pass


_XHS_LANDING_RE = re.compile(
    r'https?://(?:www\.)?xiaohongshu\.com/(?:explore|discovery/item|item)/'
    r'[0-9a-fA-F]{24}[^"\'\s<>]*xsec_token=',
    re.I,
)


def _xhs_cookie_map(cookie: str) -> dict:
    out = {}
    for part in (cookie or "").split(";"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip()
        if k:
            out[k] = v.strip()
    return out


def _xhs_resolve_short(url: str, cookie: str = "") -> str:
    """跟随 xhslink 短链，返回带 note_id + xsec_token 的落地 URL。

    不把最终跳到 /login 的地址当成功：短链常先 302 到笔记页，未带登录
    Cookie 时再被踢去登录页，requests 默认跟随会把 xsec_token 跟丢。
    """
    headers = {"User-Agent": XHS_UA, "Accept": "text/html,application/xhtml+xml"}
    cookies = _xhs_cookie_map(cookie)
    try:
        from curl_cffi import requests as cffi_requests
        sess = cffi_requests.Session(impersonate="chrome146")
        getter = sess.get
    except Exception:  # noqa: BLE001
        getter = requests.get
        sess = None
    current = url
    found = ""
    try:
        for _ in range(8):
            resp = getter(
                current, headers=headers, cookies=cookies or None,
                timeout=15, allow_redirects=False,
            )
            loc = (resp.headers.get("Location")
                   or resp.headers.get("location") or "")
            if loc:
                loc = urllib.parse.urljoin(current, loc)
                nid, tok = _xhs_ids(loc)
                if nid and tok:
                    found = loc
                    break
                current = loc
                if "/login" in urllib.parse.urlparse(current).path:
                    break
                continue
            body = ""
            try:
                body = resp.text or ""
            except Exception:  # noqa: BLE001
                body = ""
            m = _XHS_LANDING_RE.search(body)
            if m:
                found = m.group(0).rstrip(").,;]")
                break
            nid, tok = _xhs_ids(getattr(resp, "url", "") or current)
            if nid and tok:
                found = resp.url or current
            break
    except Exception:  # noqa: BLE001
        found = found or url
    finally:
        if sess is not None:
            try:
                sess.close()
            except Exception:  # noqa: BLE001
                pass
    return found or url


def _xhs_api_card_to_info(card: dict, note_id: str) -> Optional[VideoInfo]:
    """把 /feed 接口返回的 snake_case note_card 转成 VideoInfo。

    /feed 的 note_card 是 SSR noteCard 的蛇形命名版，先用 _xhs_camelize
    归一到 camelCase，再复用现有 _xhs_card_to_info 的画质去重/大小/水印逻辑。
    """
    if not isinstance(card, dict):
        return None
    got_id = card.get("note_id") or card.get("noteId")
    if not got_id:
        return None
    if note_id and str(got_id) != str(note_id):
        return None  # 串号保护
    return _xhs_card_to_info(_xhs_camelize(card))


def _xhs_camelize(obj):
    """把 snake_case 键递归转成 camelCase（note_id -> noteId 等）。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            key = str(k)
            if "_" in key:
                parts = key.split("_")
                key = parts[0] + "".join(p.title() for p in parts[1:])
            out[key] = _xhs_camelize(v)
        return out
    if isinstance(obj, list):
        return [_xhs_camelize(v) for v in obj]
    return obj


def _xhs_browser(url: str, note_id: str, token: str,
                 profile_dir: Optional[str]) -> Optional[VideoInfo]:
    """用持久化 Edge 打开笔记页并读取 SSR state"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ParseError("缺少 playwright，无法渲染小红书页面")

    from ..browser_bridge import block_heavy_resources, launch_clean

    pw = sync_playwright().start()
    ctx = None
    br = None
    try:
        # 小红书会识别 UA 覆盖 / navigator.webdriver 篡改等自动化特征，
        # 必须用原生指纹启动（套用抖音那套 stealth 参数会被跳登录页）
        # 用无头模式：解析时不留任何可见窗口（有头会"闪一下"浏览器）。
        # 实测无头下 SSR 的 noteCard 照常下发（含全新 profile 的预热路径），
        # 与有头结果完全一致。
        ctx, br = launch_clean(pw, profile_dir, True)
        # 必须复用首个页签直接 goto 笔记页：
        #   - 新建页签 / 先跳 about:blank 会被 profile 的登录墙拦截，
        #     落到 /login?redirectPath=... 拿不到 SSR 数据
        #   - 带 xsec_token 的笔记 URL 游客可直接访问
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        block_heavy_resources(page, ("image", "media", "font"))

        # ---- 关键：冷启动预热 ----
        # 实测：全新 profile 的"第一次导航"若直接打笔记页，服务端不下发
        # 含 noteCard 的 SSR（页面只有 360KB 的空壳，noteCard=0）；
        # 但先用同一个会话访问一次站点主页拿到设备 Cookie
        # （a1 / webId / web_session 等）后，同一个笔记链接 1 秒内就能拿到
        # noteCard=1 的完整 SSR（含全部图集与各档画质）。
        # 因此这里先预热：仅在缺 a1/webId 时访问首页并等 Cookie 落盘。
        if not _xhs_has_device_cookie(ctx):
            try:
                page.goto(XHS_HOST + "/explore",
                          wait_until="domcontentloaded", timeout=45000)
            except Exception:
                pass
            for _ in range(40):
                if _xhs_has_device_cookie(ctx):
                    break
                try:
                    page.wait_for_timeout(200)
                except Exception:
                    break

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            raise ParseError(f"小红书页面打开失败：{e}")

        # 小红书的 SSR script 文本在 SPA 完成路由规范化后才完整
        # （短链会 302 -> /discovery/item -> /explore/{id}）。
        # 轮询等 noteCard 出现：视频等 masterUrl，图集等 imageList。
        # 兜底也认 noteDetailMap 里的 note（部分会话只给这个槽位）。
        waited = 0
        ready = False
        while waited < 20000:
            try:
                h = page.content()
            except Exception:
                h = ""
            if (('"noteCard"' in h and ('"masterUrl"' in h
                                        or '"imageList"' in h))
                    or ('"noteDetailMap"' in h and '"noteId"' in h
                        and '"imageList"' in h)):
                ready = True
                break
            page.wait_for_timeout(150)
            waited += 150

        cur = page.url or ""
        cur_dec = urllib.parse.unquote(cur)
        # ⚠️ 重要（2026-09-28 实测）：**风控 / 登录墙的落地页里仍可能带着完整 SSR**
        # —— error_code=300012 的错误页 HTML（95052 字节）里 noteCard / imageList
        # 一应俱全，直接就能解析出图集。所以顺序必须是「先看有没有数据」，
        # 只有真的没数据时才按页面状态去报错；否则会把可用数据白白丢掉。
        if not ready:
            if "error_code=300012" in cur_dec or "IP存在风险" in cur_dec:
                raise ParseError(
                    "小红书按网络出口 IP 拒绝了本次访问"
                    "（error_code=300012「IP存在风险」）。"
                    "常见原因是系统代理 / 加速器把小红书流量送去了海外节点。"
                    "请把 xiaohongshu.com、xhslink.cn、xhscdn.com 设为直连"
                    "（或暂时关闭代理）后重试，切换网络后也可能需要等几分钟")
            if "website-login/error" in cur_dec:
                raise ParseError(
                    "小红书拦截了本次访问（网络环境被判定有风险）。"
                    "请检查代理设置：把小红书相关域名设为直连后重试")
            # 只有明确跳到登录页才算登录墙（页面 HTML 里含 /login 属正常）
            if urllib.parse.urlparse(cur).path.rstrip("/").endswith("/login"):
                raise ParseError(
                    "小红书要求登录：请在设置页点「登录 小红书」完成扫码后重试")
            if "/404" in cur and "error_code=300031" in cur:
                raise ParseError(
                    "小红书拒绝了这次访问（error_code=300031）。"
                    "该平台必须使用手机 App「分享 → 复制链接」得到的链接"
                    "（形如 xhslink.cn/o/xxxx 或 xhslink.com/a/xxxx），"
                    "网页地址栏里复制的链接无效")
            raise ParseError(
                "小红书页面未返回笔记数据。请确认链接来自 App"
                "「分享 → 复制链接」（形如 xhslink.cn/o/xxxx）；"
                "若链接确实来自 App 仍失败，多半是网络出口被风控，"
                "请关闭代理 / 加速器后重试")

        # 注意：运行时的 __INITIAL_STATE__ 是 Vue 响应式对象，含循环引用，
        # JSON.stringify 会抛 "Converting circular structure to JSON"；
        # 且实测 feed.undertakeNote.items 在运行时为空，
        # 因此以 page.content() 里的 SSR script 文本为唯一主数据源。
        html = ""
        try:
            html = page.content()
        except Exception:
            pass
    finally:
        for closer in (ctx, br):
            try:
                if closer is not None:
                    closer.close()
            except Exception:
                pass
        try:
            pw.stop()
        except Exception:
            pass

    info = None
    # 1) page.content() 的 SSR 文本（唯一可靠来源，字段最全、含最高画质）
    if html:
        info = _xhs_from_html(html, note_id)
    # 2) og 兜底（画质较低，但至少能下）
    if (info is None or (not info.play_url and not info.image_urls)) and html:
        og = _xhs_from_og(html, note_id)
        if og.play_url or og.image_urls:
            info = og
    return info


def _xhs_from_card(card: dict, note_id: str) -> Optional[VideoInfo]:
    """校验 noteId 后把 noteCard 转成 VideoInfo（串号保护）"""
    if not isinstance(card, dict) or not card.get("noteId"):
        return None
    if note_id and str(card["noteId"]) != str(note_id):
        return None
    return _xhs_card_to_info(card)


def _xhs_from_state(state: dict, note_id: str) -> Optional[VideoInfo]:
    """从 __INITIAL_STATE__ 里取 noteCard（小红书 PC 的 SSR 槽位）"""
    if not isinstance(state, dict):
        return None
    feed = state.get("feed") or {}

    items = ((feed.get("undertakeNote") or {}).get("items")) or []
    for it in items:
        if not isinstance(it, dict):
            continue
        nc = it.get("noteCard")
        if isinstance(nc, dict) and nc.get("noteId"):
            return _xhs_from_card(nc, note_id)

    # 备用：noteDetailMap（部分登录态下才有）
    ndm = state.get("noteDetailMap") or {}
    for _k, v in ndm.items():
        note = (v or {}).get("note") if isinstance(v, dict) else None
        if isinstance(note, dict) and note.get("noteId"):
            return _xhs_from_card(note, note_id)
    return None


def _xhs_from_html(html: str, note_id: str) -> Optional[VideoInfo]:
    """从页面 HTML 的 SSR script 文本里提取 noteCard

    注意：这里解析的是 page.content() 的原始文本，其中的中文/引号仍是
    原样，只有少数 \\u002F 之类的转义，_xhs_clean 处理 undefined 即可。
    优先取 `feed.undertakeNote.items[0].noteCard`，其次 noteDetailMap.note。
    """
    if not html:
        return None

    # 优先：undertakeNote 里的 noteCard
    idx = html.find('"noteCard"')
    if idx >= 0:
        card = _xhs_extract_object(html, idx)
        if card:
            got = _xhs_from_card(card, note_id)
            if got:
                return got

    # 备用：noteDetailMap 下的 note
    idx = html.find('"noteDetailMap"')
    if idx >= 0:
        m = re.search(r'"note"\s*:\s*\{', html[idx:idx + 4000])
        if m:
            card = _xhs_extract_object(html, idx + m.start() + len('"note"'))
            if card:
                got = _xhs_from_card(card, note_id)
                if got:
                    return got
    return None


def _xhs_extract_object(html: str, pos: int, limit: int = 400000) -> Optional[dict]:
    """从 html[pos] 起找最近的 { 并做括号配对 + json 解析"""
    start = html.find("{", pos)
    if start < 0:
        return None
    depth = 0
    in_str = None
    esc = False
    end = -1
    for k in range(start, min(len(html), start + limit)):
        ch = html[k]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == in_str:
                in_str = None
            continue
        if ch in ('"', "'"):
            in_str = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = k + 1
                break
    if end <= 0:
        return None
    try:
        obj = json.loads(_xhs_clean(html[start:end]))
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def _xhs_http(url: str, note_id: str, token: str,
              cookie: str = "") -> VideoInfo:
    """HTTP 直连兜底：带 xsec_token 时 SSR 可能直接下发"""
    headers = {
        "User-Agent": XHS_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": XHS_HOST + "/",
    }
    if cookie:
        headers["Cookie"] = clean_cookie(cookie)
    target = url
    if note_id and token and "xsec_token" not in url:
        target = (f"{XHS_HOST}/explore/{note_id}"
                  f"?xsec_token={urllib.parse.quote(token)}&xsec_source=pc_share")
    try:
        resp = shared_requests_session("xhs").get(
            target, headers=headers, timeout=20, allow_redirects=True)
    except requests.RequestException as e:
        raise ParseError(f"小红书请求失败：{e}")

    html = resp.text or ""
    final_url = urllib.parse.unquote(getattr(resp, "url", "") or "")
    # 同 _xhs_browser：先认数据，再认页面状态
    info = _xhs_from_html(html, note_id)
    if info is not None and (info.play_url or info.image_urls):
        return info
    if "error_code=300012" in final_url or "IP存在风险" in final_url:
        raise ParseError(
            "小红书按网络出口 IP 拒绝了本次访问"
            "（error_code=300012「IP存在风险」）。"
            "请把 xiaohongshu.com、xhslink.cn、xhscdn.com 设为直连"
            "（或暂时关闭代理）后重试")
    if info is not None:
        return info

    # 最后兜底：og 标签（画质较低，但至少能下）
    return _xhs_from_og(html, note_id)


def _xhs_clean(text: str) -> str:
    """小红书的内联 JS 对象不是严格 JSON：undefined/NaN 需替换"""
    text = re.sub(r"\bundefined\b", "null", text)
    text = re.sub(r"\bNaN\b", "null", text)
    text = re.sub(r"\bInfinity\b", "null", text)
    return text


def _xhs_from_og(html: str, note_id: str) -> VideoInfo:
    """og 标签兜底解析（og:image 是平台默认图，不可当封面）"""
    info = VideoInfo()
    info.source = "xiaohongshu"
    info.aweme_id = note_id

    m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"', html)
    if m:
        info.title = (m.group(1).replace("&amp;", "&")
                      .replace(" - 小红书", "").strip())
    m = re.search(r'<meta[^>]+property="og:description"[^>]+content="([^"]*)"',
                  html)
    if m and not info.title:
        info.title = m.group(1).replace("&amp;", "&").strip()

    for key, attr in (("note_like", "digg_count"), ("note_comment", "comment_count"),
                      ("note_collect", "share_count")):
        mm = re.search(
            r'<meta[^>]+property="og:xhs:%s"[^>]+content="([^"]*)"' % key, html)
        if mm:
            setattr(info, attr, _xhs_num(mm.group(1)))

    mv = re.search(r'<meta[^>]+property="og:video"[^>]+content="([^"]*)"', html)
    if mv:
        u = _xhs_url(mv.group(1))
        info.play_url = u
        info.play_url_candidates = [u]
        info.quality_options = [("标准画质", u)]
        info.quality = "标准画质"

    mt = re.search(r'<meta[^>]+property="og:videotime"[^>]+content="([^"]*)"',
                   html)
    if mt:
        info.duration = _xhs_duration(mt.group(1))

    if not info.play_url:
        # 图集：og:image 只在无视频时才是真实首图
        mi = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]*)"',
                       html)
        if mi:
            u = _xhs_url(mi.group(1))
            if "picasso-static" not in u:
                info.is_image = True
                info.image_urls = [u]
                info.cover_url = u
    return info


def _xhs_card_to_info(card: dict) -> VideoInfo:
    """把 noteCard（或 noteDetailMap 的 note）统一映射为 VideoInfo"""
    info = VideoInfo()
    info.source = "xiaohongshu"
    info.aweme_id = str(card.get("noteId") or "")
    info.title = (card.get("title") or "").strip()
    desc = (card.get("desc") or "").strip()
    if not info.title:
        info.title = desc[:60]

    user = card.get("user") or {}
    info.author = user.get("nickname") or ""
    info.author_uid = user.get("userId") or ""

    ii = card.get("interactInfo") or {}
    info.digg_count = _xhs_num(ii.get("likedCount"))
    info.comment_count = _xhs_num(ii.get("commentCount"))
    info.share_count = _xhs_num(ii.get("collectedCount") or ii.get("shareCount"))

    ct = card.get("time")
    try:
        info.create_time = int(ct) if ct else 0
    except (TypeError, ValueError):
        info.create_time = 0

    # 封面：优先 imageList[0].urlDefault（视频封面与图文首图都在这里）
    imgs = card.get("imageList") or []
    if imgs:
        first = imgs[0] or {}
        info.cover_url = _xhs_url(
            first.get("urlDefault") or first.get("urlPre") or "")

    video = card.get("video") or {}
    media = video.get("media") or {}
    dur = ((video.get("capa") or {}).get("duration")
           or (media.get("video") or {}).get("duration"))
    try:
        info.duration = int(float(dur) * 1000) if dur else 0
    except (TypeError, ValueError):
        info.duration = 0

    # ---- 视频流：跨 h264/h265/h266/av1 数组按 size 去重排序 ----
    stream = media.get("stream") or {}
    by_type = {}
    for _codec, arr in stream.items():
        if not isinstance(arr, list):
            continue
        for item in arr:
            if not isinstance(item, dict):
                continue
            url = _xhs_url(item.get("masterUrl") or "")
            if not url:
                bu = item.get("backupUrls") or []
                if bu:
                    url = _xhs_url(bu[0])
            if not url:
                continue
            st = item.get("streamType")
            key = st if st is not None else url
            size = int(item.get("size") or 0)
            # 同一 streamType 重复出现时保留 size 更大的一份
            old = by_type.get(key)
            if old is None or size > int(old.get("size") or 0):
                by_type[key] = dict(item, __url__=url, __size__=size)

    options, sizes = [], {}
    for item in sorted(by_type.values(),
                       key=lambda x: (x.get("__size__") or 0), reverse=True):
        w = int(item.get("width") or 0)
        h = int(item.get("height") or 0)
        codec = (item.get("videoCodec") or "").lower()
        label = f"{h}p" if h else "视频"
        if w and h and w != h:
            label = f"{w}×{h}"
        if codec == "hevc":
            label += " (H.265)"
        elif codec == "h264":
            label += " (H.264)"
        elif codec == "av1":
            label += " (AV1)"
        options.append((label, item["__url__"]))
        # 小红书 SSR 直接给了精确字节数，直接用于下拉展示
        if item.get("__size__"):
            sizes[item["__url__"]] = int(item["__size__"])

    if options:
        # 相同标签去重，避免下拉里出现两个 720p
        uniq, seen = [], set()
        for label, u2 in options:
            if label in seen:
                continue
            seen.add(label)
            uniq.append((label, u2))
        info.quality_options = uniq
        info.quality_sizes = {u: s for u, s in sizes.items()
                              if u in {x for _l, x in uniq}}
        info.play_url = uniq[0][1]
        info.play_url_candidates = [u2 for _l, u2 in uniq]
        info.quality = uniq[0][0]
        return info

    # ---- 图集 ----
    urls = []
    for im in imgs:
        u = _xhs_url((im or {}).get("urlDefault") or (im or {}).get("urlPre") or "")
        if u and u not in urls:
            urls.append(u)
    if urls:
        info.is_image = True
        info.image_urls = urls
        if not info.cover_url:
            info.cover_url = urls[0]
        # 小红书对同一张图有两种处理版本（2026-09-28 实测）：
        #   !nd_dft_wlteh_webp_3 —— 登录态下发，实测**无「小红书」水印**
        #   !nd_dft_wgth_webp_3  —— 游客态下发，右下角**带「小红书」水印**
        # 构造不了对方的地址（hash 按完整路径签名，换后缀一律 403），
        # 所以只能靠登录态，这里至少把情况记下来。
        if any("_wgth_" in u for u in urls):
            get_logger("parse").warning(
                "小红书图集下发的是「带水印」版本（!nd_dft_wgth_webp_3）："
                "服务端按游客态返回，图片右下角会带「小红书」水印。"
                "在设置页点「登录 小红书」完成扫码后重新解析，"
                "即可拿到无水印版本（!nd_dft_wlteh_webp_3）")
    return info


def _xhs_url(u: str) -> str:
    """统一转成可直连的 https 地址并去掉转义"""
    if not u:
        return ""
    # 先还原所有 JS 转义（\\u002F 是 /、\\u0026 是 &），再处理协议
    u = (u.replace("\\u002F", "/").replace("\\u002f", "/")
          .replace("\\u0026", "&").replace("\\/", "/")
          .replace("&amp;", "&").strip())
    # 去掉可能残留的反斜杠转义
    u = u.replace("\\", "")
    if u.startswith("//"):
        u = "https:" + u
    elif u.startswith("http://"):
        u = "https://" + u[7:]
    return u


def _xhs_num(v) -> int:
    """小红书互动数是中文缩写字符串：'1.6万' / '1091' / '1.2亿'"""
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().replace(",", "")
    if not s:
        return 0
    try:
        if s.endswith("亿"):
            return int(float(s[:-1]) * 100000000)
        if s.endswith("万"):
            return int(float(s[:-1]) * 10000)
        if s.endswith("w") or s.endswith("W"):
            return int(float(s[:-1]) * 10000)
        return int(float(s))
    except (ValueError, TypeError):
        return 0


def _xhs_duration(text: str) -> int:
    """'00:23' -> 23000 毫秒"""
    if not text:
        return 0
    parts = str(text).split(":")
    try:
        parts = [int(float(x)) for x in parts]
    except (ValueError, TypeError):
        return 0
    sec = 0
    for p in parts:
        sec = sec * 60 + p
    return sec * 1000
