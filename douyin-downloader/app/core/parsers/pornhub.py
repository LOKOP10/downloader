# -*- coding: utf-8 -*-
"""Pornhub 视频解析（纯 HTTP：flashvars + get_media 签名）。"""
import json
import re
import threading
import time
from typing import List

from ..http_pool import shared_requests_session
from ..models import VideoInfo
from ..parser import PC_UA, ParseError


# --------------------------------------------------------------------- #
# Pornhub（pornhub.com）
# --------------------------------------------------------------------- #
PH_HOST = "https://www.pornhub.com"
PH_VIEWKEY_RE = re.compile(r'viewkey=([A-Za-z0-9]+)', re.I)
PH_EMBED_RE = re.compile(r'pornhub\.com/embed/([A-Za-z0-9]+)', re.I)
# 整段就是一个 viewkey（13 位十六进制，可能带 ph 前缀），允许用户直接粘 ID
PH_BARE_KEY_RE = re.compile(r'(ph)?([0-9a-f]{13})', re.I)
# 该站会限流：短时间连续请求后，同样的页面会变成「能打开但拿不到 flashvars」。
# 实测隔几秒重试即可恢复，所以要退避重试而不是一次失败就报错。
PH_RETRIES = 3
PH_RETRY_WAIT = 1.5


def pornhub_viewkey(text: str) -> str:
    """从链接 / 分享文案 / 纯 viewkey 里取视频 ID，识别失败返回空串"""
    raw = (text or "").strip()
    m = PH_VIEWKEY_RE.search(raw)
    if m:
        return m.group(1)
    m = PH_EMBED_RE.search(raw)
    if m:
        return m.group(1)
    m = PH_BARE_KEY_RE.fullmatch(raw)
    if m:
        return (m.group(1) or "") + m.group(2)
    return ""


def _ph_headers() -> dict:
    return {
        "User-Agent": PC_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }


def _ph_flashvars(html: str) -> dict:
    """从页面里抠出 `flashvars_<id>` 这个 JS 对象

    它是个几千字符的大 JSON，**不能靠贪婪/懒惰正则括到结尾** —— 页面里
    还有其他 `};` 会提前截断。这里用花括号配对扫描（跳过字符串内部的括号
    与转义），拿到对象的确切边界再 json.loads。
    """
    m = re.search(r'flashvars_\d+\s*=\s*\{', html)
    if not m:
        return {}
    start = html.index("{", m.start())
    depth, in_str, esc = 0, False, False
    for i in range(start, len(html)):
        c = html[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(html[start:i + 1])
                    except Exception:  # noqa: BLE001
                        return {}
                    return data if isinstance(data, dict) else {}
    return {}


def _ph_duration_ms(html: str) -> int:
    """页面里的时长（毫秒）。`video_duration` 是秒数，ld+json 是 ISO 8601 时长"""
    m = re.search(r'"video_duration"\s*:\s*(\d+)', html)
    if m:
        return int(m.group(1)) * 1000
    m = re.search(r'"duration"\s*:\s*"PT(\d+)H(\d+)M(\d+)S"', html)
    if m:
        return (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))) * 1000
    return 0


def _ph_og_title(html: str) -> str:
    m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"', html, re.I)
    if not m:
        return ""
    import html as _html
    return _html.unescape(m.group(1)).strip()


def _ph_uploader(html: str) -> str:
    """上传者昵称（尽力而为，拿不到就留空）"""
    for pat in (r'"author"\s*:\s*"([^"]{1,80})"',
                r'class="videoUploaderLink"[^>]*>([^<]{1,80})<'):
        m = re.search(pat, html)
        if m:
            return m.group(1).strip()
    return ""


def _ph_rank(label: str) -> int:
    m = re.search(r'(\d{3,4})', label or "")
    return int(m.group(1)) if m else 0


def _ph_probe_sizes(urls: List[str], page_url: str) -> dict:
    """并发探针取每档精确字节数

    ⚠️ phncdn 的直链**不带 Referer 会回 404**，探针必须带上视频页作 Referer；
    另外它也不吃 HEAD，只能 1 字节 Range 从 Content-Range 读总长。
    """
    sizes: dict = {}
    if not urls:
        return sizes
    lock = threading.Lock()

    def probe(u: str) -> None:
        headers = {"User-Agent": PC_UA, "Referer": page_url, "Range": "bytes=0-0"}
        sess = shared_requests_session("pornhub")
        n = 0
        try:
            r = sess.get(u, headers=headers, timeout=15, allow_redirects=True)
            m = re.search(r'/(\d+)\s*$', r.headers.get("Content-Range") or "")
            n = int(m.group(1)) if m else 0
            if n <= 0 and r.status_code == 200:
                n = int(r.headers.get("content-length") or 0)
        except Exception:  # noqa: BLE001 - 拿不到就不显示大小
            n = 0
        if n > 0:
            with lock:
                sizes[u] = n

    jobs = [threading.Thread(target=probe, args=(u,), daemon=True)
            for u in dict.fromkeys(urls)]
    for job in jobs:
        job.start()
    for job in jobs:
        job.join(timeout=16)
    return sizes


def parse_pornhub(url: str) -> VideoInfo:
    """解析 Pornhub 视频（纯 HTTP，免登录）

    链路（2026-09 实测 5/5 稳定）：
      1) `GET /view_video.php?viewkey=<id>` → 页面里的 `flashvars_<id>`
      2) `mediaDefinitions` 里 `format=="mp4"` 那项是个 `get_media?s=<base64>`
         签名端点（`hls` 那几项是 m3u8，不用）
      3) `GET get_media`（**必须带视频页 Referer + X-Requested-With**）
         → 4 档 mp4 直链（240/480/720/1080），链接带 validfrom/validto 时效
      4) 并发 Range 探针取每档精确字节数

    直链落在 `ev.phncdn.com` 等 CDN，**不带 Referer 一律 404**，所以
    `domain.referer_for()` 认了 phncdn 这个域名。
    """
    vk = pornhub_viewkey(url)
    if not vk:
        raise ParseError("无法从链接中识别 Pornhub 视频 ID"
                         "（应形如 view_video.php?viewkey=xxxx）")
    page_url = f"{PH_HOST}/view_video.php?viewkey={vk}"
    # ⚠️ 全程用同一个 Session：
    #  · get_media 认会话 Cookie —— 裸 requests.get 会回**空数组**（不是报错，
    #    很容易误判成「这视频没档位」）
    #  · 站点按 IP 限流高频请求，保持同一会话更接近真实浏览器
    session = shared_requests_session("pornhub")
    ph_headers = _ph_headers()

    html, flashvars, last_err = "", {}, ""
    for attempt in range(PH_RETRIES):
        try:
            r = session.get(page_url, headers=ph_headers, timeout=(10, 30))
        except Exception as e:  # noqa: BLE001
            last_err = f"连接失败（{e}）"
            r = None
        if r is not None:
            if r.status_code == 404:
                raise ParseError(f"Pornhub 视频 {vk} 不存在或已删除")
            if r.status_code == 200:
                html = r.text
                flashvars = _ph_flashvars(html)
                if flashvars:
                    break
                last_err = "页面能打开但没带视频数据（多为限流）"
            else:
                last_err = f"页面返回 HTTP {r.status_code}"
        if attempt < PH_RETRIES - 1:
            time.sleep(PH_RETRY_WAIT * (attempt + 1))
    if not flashvars:
        raise ParseError(
            f"Pornhub 没返回视频数据：{last_err}。"
            "该站会限流，稍等几秒再试；若一直失败请确认代理能打开 pornhub.com")

    defs = flashvars.get("mediaDefinitions") or []
    endpoints = [d.get("videoUrl") for d in defs
                 if isinstance(d, dict) and (d.get("format") or "").lower() == "mp4"
                 and d.get("videoUrl")]
    if not endpoints:
        live = [d.get("quality") for d in defs if isinstance(d, dict) and d.get("format") == "hls"]
        raise ParseError(
            "该 Pornhub 视频没有可直接下载的 mp4 地址"
            + (f"（站点只给了 HLS 流 {live}，多为会员专享）" if live else "（可能已下架或受限）"))

    api_headers = dict(_ph_headers(), **{
        "Referer": page_url, "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/plain, */*"})

    rows: List[dict] = []
    for ep in endpoints:
        for attempt in range(PH_RETRIES):
            try:
                rr = session.get(ep, headers=api_headers, timeout=(10, 30))
            except Exception:  # noqa: BLE001 - 端点挂了就换下一个
                break
            if rr.status_code != 200:
                break
            try:
                data = rr.json()
            except Exception:  # noqa: BLE001
                data = None
            if isinstance(data, list) and data:
                rows.extend([x for x in data
                             if isinstance(x, dict) and x.get("videoUrl")])
                break
            # 空数组同样多为限流，退避后重试
            if attempt < PH_RETRIES - 1:
                time.sleep(PH_RETRY_WAIT * (attempt + 1))
        if rows:
            break
    if not rows:
        raise ParseError("Pornhub 没有返回可下载档位（该站会限流，稍等几秒重试）")

    seen, options = set(), []
    for item in rows:
        u = item["videoUrl"]
        if u in seen:
            continue
        seen.add(u)
        q = str(item.get("quality") or "").strip()
        options.append((f"{q}p" if q.isdigit() else (q or "视频"), u))
    options.sort(key=lambda it: _ph_rank(it[0]), reverse=True)
    if not options:
        raise ParseError("Pornhub 未解析到可用的下载地址")

    info = VideoInfo()
    info.source = "pornhub"
    info.aweme_id = vk
    info.title = ((flashvars.get("video_title") or "").strip()
                  or _ph_og_title(html) or vk)
    info.author = _ph_uploader(html)
    info.duration = _ph_duration_ms(html)
    info.cover_url = (flashvars.get("image_url") or "").strip()
    info.quality_options = options
    info.play_url = options[0][1]
    info.quality = options[0][0]
    info.play_url_candidates = [u for _lab, u in options]

    sizes = _ph_probe_sizes([u for _lab, u in options], page_url)
    if sizes:
        info.quality_sizes.update(sizes)
    return info
