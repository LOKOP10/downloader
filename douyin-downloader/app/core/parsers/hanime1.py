# -*- coding: utf-8 -*-
"""hanime1.me 番剧解析（curl_cffi 拟真指纹直连）。"""
import re
import threading
import time
from typing import List, Tuple

from ..http_pool import cffi_proxies, shared_cffi_session, shared_requests_session
from ..models import VideoInfo
from ..parser import PC_UA, ParseError


# --------------------------------------------------------------------- #
# hanime1.me（hanime1.me）—— 成年向番剧，免登录
# --------------------------------------------------------------------- #
HN_HOST = "https://hanime1.me"
# 只认 /watch?v=<id>。实测：路径式 /watch/<id> 与 /v/<id> 都是 404，
# 参数名换成 ?id= 直接 403 —— 别按"直觉"多写几种形式，站点不认。
HN_ID_RE = re.compile(r'/watch\?v=([A-Za-z0-9_\-]+)', re.I)
HN_BARE_ID_RE = re.compile(r'^(\d{4,10})$')
HN_TITLE_SUFFIX = " - Hanime1.me"
HN_RETRIES = 2
HN_RETRY_WAIT = 1.2


def hanime1_video_id(text: str) -> str:
    """从链接或纯数字 id 里取视频 id，识别失败返回空串"""
    raw = (text or "").strip()
    m = HN_ID_RE.search(raw)
    if m:
        return m.group(1)
    m = HN_BARE_ID_RE.fullmatch(raw)
    return m.group(1) if m else ""


def _hn_session():
    """建会话。

    ⚠️ 这个站点在 Cloudflare 后面且**认 TLS 指纹**：python-requests 的
    ClientHello 会被直接断开（`SSLError: UNEXPECTED_EOF_WHILE_READING`），
    同一台机器上 curl 却能正常 200。必须用 curl_cffi 的浏览器指纹。
    curl_cffi 已在打包清单里（禁漫也依赖它），所以不算新增依赖。

    注意只有**页面**需要这层指纹；视频直链所在的 vdownload.hembed.com
    用普通 requests 就能下（HEAD / 1 字节 Range 都正常，也不吃 Referer），
    所以下载链路不用改。
    """
    try:
        session = shared_cffi_session("hanime1", impersonate="chrome")
    except ImportError as e:  # pragma: no cover - 打包时必带
        raise ParseError(f"缺少 curl_cffi 组件，无法解析 hanime1（{e}）") from e
    session.headers.update({
        "User-Agent": PC_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    })
    return session


def _hn_sources(html: str) -> List[Tuple[int, str]]:
    """抠出所有 <source type="video/mp4">，返回 [(档位高度, 地址)]。

    属性顺序不保证（src/size/type 谁先谁后都可能），所以逐个标签内部单独
    取属性，不写成一整条正则。size 是**档位高度**（1080/720/480），
    不是字节数；页面里的排列顺序是 720 → 480 → 1080，必须自己排。
    """
    out: List[Tuple[int, str]] = []
    for tag in re.findall(r'<source\b[^>]*>', html, re.I):
        m = re.search(r'src="([^"]+)"', tag, re.I)
        if not m:
            continue
        url = m.group(1).strip()
        if ".mp4" not in url.lower():
            continue
        s = re.search(r'size="(\d+)"', tag, re.I)
        out.append((int(s.group(1)) if s else 0, url))
    return out


def _hn_title(html: str) -> str:
    """标题：详情块的 h3，兜底 og:title 去掉 ' - Hanime1.me' 后缀"""
    import html as _html
    m = re.search(r'<h3[^>]*id="shareBtn-title"[^>]*>(.*?)</h3>', html, re.S | re.I)
    if m:
        text = _html.unescape(re.sub(r'<[^>]+>', '', m.group(1))).strip()
        if text:
            return _html.unescape(text)
    m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"', html, re.I)
    if not m:
        return ""
    title = _html.unescape(m.group(1)).strip()
    if title.endswith(HN_TITLE_SUFFIX):
        title = title[:-len(HN_TITLE_SUFFIX)].strip()
    return title


def _hn_author(html: str) -> str:
    """上传者：详情块的 a#video-artist-name，兜底头像 img 的 alt"""
    import html as _html
    m = re.search(r'<a[^>]*id="video-artist-name"[^>]*>(.*?)</a>', html, re.S | re.I)
    if m:
        text = _html.unescape(re.sub(r'<[^>]+>', '', m.group(1))).strip()
        if text:
            return text
    m = re.search(r'<img[^>]*id="video-user-avatar"[^>]*alt="([^"]*)"', html, re.I)
    if not m:
        m = re.search(r'<img[^>]*alt="([^"]*)"[^>]*id="video-user-avatar"', html, re.I)
    return _html.unescape(m.group(1)).strip() if m else ""


def _hn_cover(html: str) -> str:
    """封面：<video poster> 优先（带 secure 签名），兜底 og:image"""
    m = re.search(r'<video[^>]+poster="([^"]+)"', html, re.I)
    if m and m.group(1).startswith("http"):
        return m.group(1)
    m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]*)"', html, re.I)
    return m.group(1).strip() if m else ""


def _hn_probe_sizes(urls: List[str]) -> dict:
    """并发探针取每档精确字节数。

    直链带 `?secure=<签名>,<过期时间>`，**签名是强制的**（去掉 query 或改一个
    字符都回 403），所以只能用页面刚给的那条地址。
    该 CDN 支持 HEAD 也支持 Range，这里用 1 字节 Range（更保险，个别节点挡 HEAD），
    并且**不需要 Referer / Cookie**，所以直接用普通 requests。
    """
    sizes: dict = {}
    if not urls:
        return sizes
    lock = threading.Lock()

    def probe(u: str) -> None:
        n = 0
        try:
            r = shared_requests_session("hanime1_cdn").get(
                u, headers={"User-Agent": PC_UA, "Range": "bytes=0-0"},
                timeout=15, allow_redirects=True)
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


def parse_hanime1(url: str) -> VideoInfo:
    """解析 hanime1.me 视频（纯 HTTP + curl_cffi 指纹，免登录）

    链路（2026-09 实测）：
      1) `GET /watch?v=<id>` —— **必须 curl_cffi impersonate**（见 _hn_session）
      2) 页面 `<video>` 里直接挂着 3 个 `<source type="video/mp4" size="…">`
         （1080 / 720 / 480，HTML 里的顺序是乱的），指向
         `vdownload.hembed.com/<id>-<画质>p.mp4?secure=<签名>,<过期>`
      3) 并发 1 字节 Range 取每档精确字节数

    已知边界：
      - 页面**没有时长字段**（详情块和 JSON 里都翻过），所以 duration 留 0，
        界面显示 `--:--`。宁可不显示，也别去猜一个错的。
      - 直链是**签名 + 时效**的：解析后搁太久再下会 403。重试/续传依赖重新解析。
      - 没有备用 CDN 地址，所以 url_backups 为空。
    """
    vid = hanime1_video_id(url)
    if not vid:
        raise ParseError("无法从链接中识别 hanime1 视频 ID"
                         "（应形如 hanime1.me/watch?v=123456）")
    page_url = f"{HN_HOST}/watch?v={vid}"
    session = _hn_session()

    proxies = cffi_proxies(page_url)
    html, last_err = "", ""
    for attempt in range(HN_RETRIES):
        try:
            kwargs = {"timeout": (10, 30)}
            if proxies:
                kwargs["proxies"] = proxies
            r = session.get(page_url, **kwargs)
        except Exception as e:  # noqa: BLE001
            last_err = f"连接失败（{type(e).__name__}: {e}）"
            r = None
        if r is not None:
            if r.status_code == 404:
                raise ParseError(f"hanime1 视频 {vid} 不存在或已删除")
            if r.status_code == 200:
                html = r.text
                if _hn_sources(html):
                    break
                last_err = "页面能打开但没有视频源（可能被风控拦了）"
            else:
                last_err = f"页面返回 HTTP {r.status_code}"
        if attempt < HN_RETRIES - 1:
            time.sleep(HN_RETRY_WAIT * (attempt + 1))

    sources = _hn_sources(html)
    if not sources:
        raise ParseError(
            f"hanime1 没返回可下载的视频源：{last_err}。"
            "该站有 Cloudflare 风控，稍后重试；若一直失败请确认代理能打开 hanime1.me")

    # size 属性是档位高度（1080/720/480），直接拿来当画质标签并按高度降序
    seen, options = set(), []
    for height, u in sources:
        if u in seen:
            continue
        seen.add(u)
        options.append((f"{height}p" if height else "视频", u))
    options.sort(key=lambda it: int(it[0][:-1]) if it[0].endswith("p") and
                 it[0][:-1].isdigit() else 0, reverse=True)
    if not options:
        raise ParseError("hanime1 未解析到可用的下载地址")

    info = VideoInfo()
    info.source = "hanime1"
    info.aweme_id = vid
    info.title = _hn_title(html) or vid
    info.author = _hn_author(html)
    info.duration = 0          # 站点不提供时长，见 docstring
    info.cover_url = _hn_cover(html)
    info.quality_options = options
    info.play_url = options[0][1]
    info.quality = options[0][0]
    info.play_url_candidates = [u for _lab, u in options]

    sizes = _hn_probe_sizes([u for _lab, u in options])
    if sizes:
        info.quality_sizes.update(sizes)
    return info
