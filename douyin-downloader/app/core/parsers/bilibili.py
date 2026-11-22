# -*- coding: utf-8 -*-
"""Bilibili 单作品解析（wbi 签名 API + DASH / durl 候选排序）。"""
import hashlib
import re
import threading
import time
import urllib.parse
from typing import List, Optional, Tuple

import requests

from ..http_pool import shared_requests_session
from ..models import VideoInfo, clean_cookie
from ..parser import PC_UA, ParseError


# --------------------------------------------------------------------- #
# Bilibili
# --------------------------------------------------------------------- #
BILI_API = "https://api.bilibili.com"

# WBI 签名用的重排表（B 站前端固定值）
_BILI_MIXIN_TAB = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
                   27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
                   37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
                   22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52]
_bili_wbi_cache = {"keys": None, "ts": 0}
_bili_buvid_cache = {"cookie": "", "ts": 0}
_bili_buvid_lock = threading.Lock()


def _bili_guest_buvid() -> str:
    """从 /x/frontend/finger/spi 拿游客 buvid3/buvid4 并缓存 12 小时，避免匿名请求触发风控降级。"""
    now = time.time()
    with _bili_buvid_lock:
        if _bili_buvid_cache["cookie"] and now - _bili_buvid_cache["ts"] < 43200:
            return _bili_buvid_cache["cookie"]
    try:
        sess = shared_requests_session("bilibili")
        resp = sess.get(f"{BILI_API}/x/frontend/finger/spi", headers={
            "User-Agent": PC_UA,
            "Referer": "https://www.bilibili.com/",
        }, timeout=8)
        payload = resp.json() if resp.ok else {}
        data = payload.get("data") or {}
        b3, b4 = data.get("b_3") or "", data.get("b_4") or ""
        if payload.get("code") == 0 and b3:
            val = f"buvid3={b3}; b_nut={int(now)}" + (f"; buvid4={b4}" if b4 else "")
            with _bili_buvid_lock:
                _bili_buvid_cache["cookie"] = val
                _bili_buvid_cache["ts"] = now
            return val
    except Exception:  # noqa: BLE001
        pass
    return ""


def _bili_headers(cookie: str = "") -> dict:
    headers = {
        "User-Agent": PC_UA,
        "Referer": "https://www.bilibili.com/",
        "Origin": "https://www.bilibili.com",
        "Accept": "application/json, text/plain, */*",
    }
    eff = clean_cookie(cookie) if cookie else _bili_guest_buvid()
    if eff:
        headers["Cookie"] = eff
    return headers


def _bili_get(path: str, params: dict, cookie: str = "",
              signed: bool = False) -> dict:
    if signed:
        params = _bili_sign_params(params, cookie)
    try:
        resp = shared_requests_session("bilibili").get(
            BILI_API + path, params=params,
            headers=_bili_headers(cookie), timeout=15)
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        raise ParseError(f"B 站接口请求失败：{e}")
    if data.get("code") != 0:
        raise ParseError(f"B 站接口返回：{data.get('message') or data.get('code')}")
    return data.get("data") or {}


def _bili_wbi_keys(cookie: str = "") -> Optional[Tuple[str, str]]:
    """取 wbi 签名密钥（来自 nav 接口，缓存 1 小时）"""
    now = time.time()
    if _bili_wbi_cache["keys"] and now - _bili_wbi_cache["ts"] < 3600:
        return _bili_wbi_cache["keys"]
    try:
        data = _bili_get("/x/web-interface/nav", {}, cookie)
        wbi = data.get("wbi_img") or {}
        img = (wbi.get("img_url") or "").rsplit("/", 1)[-1].split(".")[0]
        sub = (wbi.get("sub_url") or "").rsplit("/", 1)[-1].split(".")[0]
        if img and sub:
            _bili_wbi_cache["keys"] = (img, sub)
            _bili_wbi_cache["ts"] = now
            return img, sub
    except ParseError:
        pass
    return None


def _bili_sign_params(params: dict, cookie: str = "") -> dict:
    """给请求参数加 wts + w_rid（B 站 wbi 签名），否则拿不到完整清晰度"""
    keys = _bili_wbi_keys(cookie)
    if not keys:
        return params
    img_key, sub_key = keys
    raw = img_key + sub_key
    mixin = "".join(raw[i] for i in _BILI_MIXIN_TAB)[:32]

    signed = dict(params)
    signed["wts"] = int(time.time())
    items = sorted(signed.items())
    query = urllib.parse.urlencode([
        (k, "".join(ch for ch in str(v) if ch not in "!'()*")) for k, v in items])
    signed["w_rid"] = hashlib.md5((query + mixin).encode()).hexdigest()
    return signed


def _bili_play_url(bvid: str, cid: int, qn: int, cookie: str) -> str:
    """按指定清晰度取 durl 地址（fnval=1 → 音视频合一的 mp4，无需 ffmpeg 合并）"""
    url, _sz, _alts = _bili_play_url_size(bvid, cid, qn, cookie)
    return url


def _bili_cdn_rank(url: str) -> int:
    """B 站 CDN 节点优选排序值（越小越优先）：upos 镜像 > 普通 CDN > PCDN/MCDN。"""
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    query = (parsed.query or "").lower()
    # 实测 PCDN / MCDN 节点（如 cn-jsnj-fx-*、*.mcdn.*、os=bcache/mcdn）经常 TLS 重置
    # 或死等超时，而 upos-* 官方镜像一直稳定高速；仅将 PCDN 沉底作兜底，绝不丢弃。
    if (".mcdn." in host or host.startswith("cn-") or host.startswith("mcdn-")
            or "os=bcache" in query or "os=mcdn" in query):
        return 2
    if host.startswith("upos-"):
        return 0
    return 1


def _bili_play_url_size(bvid: str, cid: int, qn: int,
                        cookie: str) -> Tuple[str, int, List[str]]:
    """按清晰度取 (durl 地址, 字节数, 备用地址)。durl 项自带精确 size"""
    data = _bili_get("/x/player/wbi/playurl", {
        "bvid": bvid, "cid": cid, "qn": qn, "fnval": 1, "fourk": 1,
        "platform": "pc", "otype": "json", "high_quality": 1,
    }, cookie, signed=True)
    durl = data.get("durl") or []
    if not durl:
        return "", 0, []
    try:
        sz = int(durl[0].get("size") or 0)
    except (TypeError, ValueError):
        sz = 0
    cands = _bili_stream_urls(durl[0])
    return (cands[0] if cands else ""), sz, cands[1:]


def _bili_stream_urls(stream: dict) -> List[str]:
    """一路流（视频 / 音频 / durl）的全部候选地址，优先官方 upos 节点。

    实测 baseUrl 指向的 PCDN 节点（cn-jsnj-fx-*）经常直接 TLS 重置
    （SSL EOF）或连不上，而 backupUrl 里的 upos-* 镜像一直稳定（9~10 MB/s）。
    只认 baseUrl 时主地址一挂整单就卡住，两者都要保留并优先 upos 镜像。
    """
    out: List[str] = []
    for key in ("baseUrl", "base_url", "url"):
        if stream.get(key):
            out.append(stream[key])
    out.extend(_bili_backup_urls(stream))
    uniq: List[str] = []
    for u in out:
        if u and u not in uniq:
            uniq.append(u)
    return sorted(uniq, key=_bili_cdn_rank)


def _bili_backup_urls(item: dict, skip: str = "") -> List[str]:
    """取一项流里的 backupUrl 列表（去掉与主地址相同的）"""
    out: List[str] = []
    for key in ("backupUrl", "backup_url"):
        for u in (item.get(key) or []):
            if u and u != skip and u not in out:
                out.append(u)
    return out


def _bili_fps(stream: dict) -> int:
    """B 站 DASH 视频流里的帧率；拿不到返回 0

    `frameRate` / `frame_rate` 既有 `"60"` 这种整数，也有 `"16000/672"`
    这种分数（23.976 / 29.97 / 59.94 的真实写法），分数要先除再取整。
    拿到帧率就能把它拼进画质标签，下拉里「有 60fps 就不显示 30fps」的规则
    才认得出来（B 站清晰度描述本身只有 `1080P 高码率` 这种，不带帧率）。
    """
    for key in ("frameRate", "frame_rate"):
        raw = stream.get(key)
        if raw in (None, ""):
            continue
        text = str(raw).strip()
        try:
            value = (float(text.split("/")[0]) / float(text.split("/")[1])
                     if "/" in text else float(text))
        except (ValueError, ZeroDivisionError, IndexError):
            continue
        if value > 0:
            return int(round(value))
    return 0


def parse_bilibili(url: str, cookie: str = "",
                   base_dir: str = "") -> VideoInfo:
    """解析 B 站视频（BV/av 号，支持分 P），默认最高可获得清晰度

    有 ffmpeg 时走 DASH（可达 1080P/1080P+，视频音频分离后合并）；
    否则走 durl（音视频已合并的 mp4，最高 720P）。
    """
    # 短链还原
    if "b23.tv" in urllib.parse.urlparse(url).netloc.lower():
        try:
            resp = shared_requests_session("bilibili").get(
                url, headers=_bili_headers(),
                timeout=15, allow_redirects=True)
            url = resp.url
        except requests.RequestException as e:
            raise ParseError(f"短链解析失败：{e}")

    params = {}
    m = re.search(r'/(BV[0-9A-Za-z]{10})', url)
    if m:
        params["bvid"] = m.group(1)
    else:
        m = re.search(r'/av(\d+)', url, re.I)
        if not m:
            raise ParseError("无法从链接中识别 B 站视频号（BV/av）")
        params["aid"] = m.group(1)

    view = _bili_get("/x/web-interface/view", params, cookie)
    bvid = view.get("bvid") or params.get("bvid") or ""
    if not bvid:
        raise ParseError("B 站接口未返回视频信息")

    pages = view.get("pages") or []
    if not pages:
        raise ParseError("该视频没有可用分 P")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    try:
        pno = int((q.get("p") or ["1"])[0])
    except (TypeError, ValueError):
        pno = 1
    if not 1 <= pno <= len(pages):
        pno = 1
    page = pages[pno - 1]
    cid = page.get("cid") or view.get("cid")

    owner = view.get("owner") or {}
    stat = view.get("stat") or {}
    info = VideoInfo()
    info.source = "bilibili"
    info.aweme_id = bvid
    part = page.get("part") or ""
    info.title = view.get("title") or ""
    if len(pages) > 1:
        info.title = f"{info.title} P{pno}{(' ' + part) if part else ''}"
    info.author = owner.get("name") or ""
    info.cover_url = view.get("pic") or ""
    info.duration = int((page.get("duration") or view.get("duration") or 0)) * 1000
    info.digg_count = int(stat.get("like") or 0)
    info.comment_count = int(stat.get("reply") or 0)
    info.share_count = int(stat.get("share") or 0)

    # ---- 优先 DASH（有 ffmpeg 才能合并出 1080P+）----
    from ..ffmpeg import ffmpeg_path
    if ffmpeg_path(base_dir):
        try:
            dash = _bili_get("/x/player/wbi/playurl", {
                "bvid": bvid, "cid": cid, "qn": 127, "fnval": 4048, "fourk": 1,
                "platform": "pc", "otype": "json", "high_quality": 1,
            }, cookie, signed=True)
            d = dash.get("dash") or {}
            vstreams = d.get("video") or []
            astreams = d.get("audio") or []
            if vstreams and astreams:
                def _bw(x):
                    try:
                        return int(x.get("bandwidth") or 0)
                    except (TypeError, ValueError):
                        return 0
                astream = max(astreams, key=_bw)
                a_cands = _bili_stream_urls(astream)
                audio_url = a_cands[0] if a_cands else ""
                audio_bw = _bw(astream)
                qmap = dict(zip(dash.get("accept_quality") or [],
                                dash.get("accept_description") or []))
                options, audio_map, sizes = [], {}, {}
                url_backups, audio_backups = {}, {}
                if len(a_cands) > 1:
                    audio_backups[audio_url] = a_cands[1:]
                for qid in sorted(qmap.keys(), reverse=True):
                    cands = [v for v in vstreams
                             if int(v.get("id") or 0) == int(qid)]
                    if not cands:
                        continue
                    # 同档位优先 AVC（兼容性最好），再按码率取高
                    avc = [v for v in cands if "avc1" in (v.get("codecs") or "")]
                    pick = max(avc or cands, key=_bw)
                    v_cands = _bili_stream_urls(pick)
                    vurl = v_cands[0] if v_cands else ""
                    if not vurl:
                        continue
                    # baseUrl 挂在 PCDN 节点上，挂了就换 backupUrl 的镜像
                    if len(v_cands) > 1:
                        url_backups[vurl] = v_cands[1:]
                    label = f"{qmap.get(qid) or qid} {pick.get('width')}×{pick.get('height')}"
                    # 帧率拼进标签：B 站清晰度描述（1080P 高码率）本身不带帧率，
                    # 只有 DASH 流里才有，下拉的「有 60fps 就不显示 30fps」靠它
                    fps = _bili_fps(pick)
                    if fps:
                        label += f" · {fps}fps"
                    if int(qid) == max(qmap.keys()):
                        label += "（最高）"
                    options.append((label, vurl))
                    # DASH 无字节数，用 (视频+音频) 码率 × 时长估算大小
                    vbw = _bw(pick)
                    if info.duration > 0 and vbw > 0:
                        est = (vbw + audio_bw) * (info.duration / 1000.0) / 8.0
                        if est > 0:
                            sizes[vurl] = int(est)
                    if audio_url:
                        audio_map[vurl] = audio_url
                if options:
                    info.quality_options = options
                    info.quality_sizes = sizes
                    info.play_url_candidates = [u for _, u in options]
                    info.play_url = options[0][1]
                    info.quality = options[0][0]
                    info.audio_map = audio_map
                    info.url_backups = url_backups
                    info.audio_backups = audio_backups
                    return info
        except ParseError:
            pass  # DASH 失败则回退 durl

    # ---- 回退：durl（音视频合并 mp4，最高 720P）----
    play = _bili_get("/x/player/wbi/playurl", {
        "bvid": bvid, "cid": cid, "qn": 127, "fnval": 1, "fourk": 1,
        "platform": "pc", "otype": "json", "high_quality": 1,
    }, cookie, signed=True)
    qmap = dict(zip(play.get("accept_quality") or [],
                    play.get("accept_description") or []))
    current_qn = int(play.get("quality") or 0)
    durl = play.get("durl") or []
    if not durl:
        raise ParseError("未获取到播放地址，该视频可能需要登录或为付费内容")

    options = []
    sizes = {}
    url_backups = {}
    for qn in sorted(qmap.keys(), reverse=True):
        qn = int(qn)
        try:
            if qn == current_qn and durl:
                # 当前档位直接用响应里的 durl，含精确 size，并优先 upos 节点
                cands = _bili_stream_urls(durl[0])
                u = cands[0] if cands else ""
                alts = cands[1:]
                try:
                    dsz = int(durl[0].get("size") or 0)
                except (TypeError, ValueError):
                    dsz = 0
            else:
                u, dsz, alts = _bili_play_url_size(bvid, cid, qn, cookie)
        except ParseError:
            continue
        if u:
            label = qmap.get(qn) or f"{qn}"
            if qn == max(qmap.keys(), default=0):
                label += "（最高）"
            options.append((label, u))
            if alts:
                url_backups[u] = alts
            if dsz:
                sizes[u] = dsz
    if not options:
        raise ParseError("未能获取可用清晰度，请确认 B 站登录态是否有效")

    info.quality_options = options
    info.quality_sizes = sizes
    info.play_url_candidates = [u for _, u in options]
    info.play_url = options[0][1]
    info.quality = options[0][0]
    info.url_backups = url_backups
    return info
