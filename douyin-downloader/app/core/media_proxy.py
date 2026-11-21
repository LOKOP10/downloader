# -*- coding: utf-8 -*-
"""预览媒体的本地代理：连接池复用、顺序分块预读、请求头与备用源。"""

from collections import OrderedDict
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests
from requests.adapters import HTTPAdapter

from .domain import needs_cookie_for_url
from .http_headers import headers_for
from .models import clean_cookie

_STREAM_CHUNK = 256 * 1024
_PREFETCH_BLOCK = 1024 * 1024       # 1MB 对齐预读块
_PREFETCH_MIN_TOTAL = 512 * 1024    # 仅对 >=512KB 的大流启用预读缓存，小流原样透传
_PREFETCH_AHEAD = 2                 # 每次命中/响应后向前预拉最多 2 块（2MB）
_MAX_CACHE_BLOCKS = 10              # 单代理实例最多缓存 10MB
_RANGE_RE = re.compile(r"^bytes=(\d+)-(\d*)$")
_THREAD_LOCAL = threading.local()


def _proxy_session() -> requests.Session:
    """每个处理线程独享一个带连接池的 Session，避免并发共享 CookieJar 竞争。"""
    sess = getattr(_THREAD_LOCAL, "session", None)
    if sess is None:
        sess = requests.Session()
        adapter = HTTPAdapter(pool_connections=8, pool_maxsize=16)
        sess.mount("http://", adapter)
        sess.mount("https://", adapter)
        _THREAD_LOCAL.session = sess
    return sess


def _parse_total_size(headers) -> int:
    cr = headers.get("Content-Range") or ""
    if "/" in cr:
        tail = cr.rsplit("/", 1)[-1].strip()
        if tail.isdigit():
            return int(tail)
    cl = (headers.get("Content-Length") or "").strip()
    return int(cl) if cl.isdigit() else 0


def media_headers(url, cookies="", byte_range=""):
    """预览与下载使用同一请求头和 Cookie 域名规则。"""
    headers = headers_for(url)
    if cookies and needs_cookie_for_url(url):
        headers["Cookie"] = clean_cookie(cookies)
    if byte_range:
        headers["Range"] = byte_range
    return headers


class LocalMediaProxy(ThreadingHTTPServer):
    """仅监听回环地址的临时服务；调用方负责关闭。"""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, tracks, cookies):
        super().__init__(("127.0.0.1", 0), _MediaHandler)
        self.tracks = tracks
        self.cookies = cookies
        self.token = secrets.token_urlsafe(24)
        self._stopped = threading.Event()
        self._cache_lock = threading.Lock()
        self._blocks: OrderedDict = OrderedDict()
        self._inflight = set()
        self._track_meta = {}       # track -> (total_size, content_type)
        self._url_order = {track: list(urls) for track, urls in tracks.items()}
        self.thread = threading.Thread(target=self.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def url(self, track):
        """返回供播放器使用的本地地址。"""
        return f"http://127.0.0.1:{self.server_port}/{self.token}/{track}"

    def candidate_urls(self, track: str):
        """返回当前按健康度排序的候选源列表；同步外部对 tracks 的修改。"""
        raw = list(self.tracks.get(track) or [])
        with self._cache_lock:
            cached = self._url_order.get(track)
            if cached is not None and len(cached) == len(raw) and set(cached) == set(raw):
                return list(cached)
            self._url_order[track] = list(raw)
            return raw

    def report_url(self, track: str, target_url: str, ok: bool):
        """根据实测结果调整轨道候选源优先级，避免后续分块反复踩死节点。"""
        if not target_url:
            return
        with self._cache_lock:
            order = self._url_order.get(track)
            if not order or target_url not in order:
                return
            if ok:
                if order[0] != target_url:
                    order.remove(target_url)
                    order.insert(0, target_url)
            elif len(order) > 1 and order[-1] != target_url:
                order.remove(target_url)
                order.append(target_url)

    def record_track_meta(self, track: str, url: str, total: int, content_type: str):
        if total >= _PREFETCH_MIN_TOTAL and url:
            with self._cache_lock:
                self._track_meta[track] = (total, content_type or "video/mp4")

    def get_cached_range(self, track: str, start: int, end: int):
        """若请求区间完整落入已对齐预读块中，返回 (bytes, total, content_type)。"""
        with self._cache_lock:
            meta = self._track_meta.get(track)
            if not meta or start < 0 or end < start or (end - start + 1) > _PREFETCH_BLOCK * 2:
                return None
            total, ctype = meta
            if start >= total:
                return None
            end = min(end, total - 1)
            b_start = start // _PREFETCH_BLOCK
            b_end = end // _PREFETCH_BLOCK
            chunks = []
            for idx in range(b_start, b_end + 1):
                blk = self._blocks.get((track, idx))
                if blk is None:
                    return None
                self._blocks.move_to_end((track, idx))
                seg_s = max(start, idx * _PREFETCH_BLOCK) - idx * _PREFETCH_BLOCK
                seg_e = min(end, idx * _PREFETCH_BLOCK + len(blk) - 1) - idx * _PREFETCH_BLOCK
                if seg_e < seg_s:
                    return None
                chunks.append(blk[seg_s:seg_e + 1])
            return b"".join(chunks), total, ctype

    def schedule_prefetch(self, track: str, offset: int):
        """在后台按 1MB 块预拉 offset 之后的顺序分块。"""
        if self._stopped.is_set():
            return
        with self._cache_lock:
            meta = self._track_meta.get(track)
            if not meta:
                return
            total, _ = meta
            start_blk = max(0, offset // _PREFETCH_BLOCK)
            targets = []
            for idx in range(start_blk, start_blk + _PREFETCH_AHEAD):
                if idx * _PREFETCH_BLOCK >= total:
                    break
                key = (track, idx)
                if key not in self._blocks and key not in self._inflight:
                    self._inflight.add(key)
                    targets.append(idx)
        for idx in targets:
            threading.Thread(
                target=self._fetch_block,
                args=(track, total, idx),
                daemon=True,
            ).start()

    def _fetch_block(self, track: str, total: int, idx: int):
        key = (track, idx)
        try:
            if self._stopped.is_set():
                return
            start = idx * _PREFETCH_BLOCK
            end = min(total - 1, start + _PREFETCH_BLOCK - 1)
            if end < start:
                return
            sess = _proxy_session()
            urls = self.candidate_urls(track)
            for pos, target_url in enumerate(urls):
                if self._stopped.is_set():
                    return
                timeout = (3.5, 10) if (pos + 1 < len(urls)) else (6, 15)
                try:
                    resp = sess.get(
                        target_url,
                        headers=media_headers(target_url, self.cookies, f"bytes={start}-{end}"),
                        timeout=timeout,
                    )
                    if resp.status_code in (200, 206) and resp.content:
                        data = resp.content
                        if len(data) <= _PREFETCH_BLOCK + 1024 and not self._stopped.is_set():
                            self.report_url(track, target_url, True)
                            with self._cache_lock:
                                self._blocks[key] = data
                                self._blocks.move_to_end(key)
                                while len(self._blocks) > _MAX_CACHE_BLOCKS:
                                    self._blocks.popitem(last=False)
                        return
                    self.report_url(track, target_url, False)
                except requests.RequestException:
                    self.report_url(track, target_url, False)
                    continue
        except Exception:  # noqa: BLE001 - 预读失败不影响主流程
            pass
        finally:
            with self._cache_lock:
                self._inflight.discard(key)

    def stop(self):
        self._stopped.set()
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=1)
        with self._cache_lock:
            self._blocks.clear()
            self._track_meta.clear()
            self._url_order.clear()


class _MediaHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def do_HEAD(self):
        self._serve(head_only=True)

    def do_GET(self):
        self._serve(head_only=False)

    def _serve(self, head_only=False):
        parts = self.path.split("?")[0].split("/")
        if len(parts) != 3 or parts[1] != self.server.token or parts[2] not in self.server.tracks:
            self.send_error(404)
            return
        track = parts[2]
        urls = self.server.candidate_urls(track)
        byte_range = self.headers.get("Range", "")
        m_range = _RANGE_RE.match(byte_range.strip()) if byte_range else None

        if not head_only and m_range:
            r_start = int(m_range.group(1))
            r_end_str = m_range.group(2)
            if r_end_str:
                r_end = int(r_end_str)
                hit = self.server.get_cached_range(track, r_start, r_end)
                if hit is not None:
                    payload, total, ctype = hit
                    actual_end = r_start + len(payload) - 1
                    self.send_response(206)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Content-Range", f"bytes {r_start}-{actual_end}/{total}")
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    try:
                        self.wfile.write(payload)
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        return
                    self.server.schedule_prefetch(track, actual_end + 1)
                    return

        sess = _proxy_session()
        total_cands = len(urls)
        for idx, url in enumerate(urls):
            req_timeout = (3.5, 8) if (idx + 1 < total_cands) else (8, 20)
            sent_headers = False
            try:
                with sess.request("HEAD" if head_only else "GET", url,
                                  headers=media_headers(url, self.server.cookies, byte_range),
                                  stream=True, timeout=req_timeout) as upstream:
                    if upstream.status_code not in (200, 206):
                        self.server.report_url(track, url, False)
                        continue
                    if byte_range and upstream.status_code != 206:
                        self.server.report_url(track, url, False)
                        continue
                    self.server.report_url(track, url, True)
                    total = _parse_total_size(upstream.headers)
                    ctype = upstream.headers.get("Content-Type") or "video/mp4"
                    if total >= _PREFETCH_MIN_TOTAL and (
                        upstream.status_code == 206
                        or (upstream.headers.get("Accept-Ranges") or "").lower() == "bytes"
                    ):
                        self.server.record_track_meta(track, url, total, ctype)

                    sent_headers = True
                    self.send_response(upstream.status_code)
                    for name in ("Content-Type", "Content-Length", "Content-Range",
                                 "Accept-Ranges"):
                        value = upstream.headers.get(name)
                        if value:
                            self.send_header(name, value)
                    self.send_header("Connection", "close")
                    self.end_headers()
                    if not head_only:
                        sent = 0
                        for chunk in upstream.iter_content(_STREAM_CHUNK):
                            if chunk:
                                self.wfile.write(chunk)
                                sent += len(chunk)
                        if m_range and total >= _PREFETCH_MIN_TOTAL and sent > 0:
                            self.server.schedule_prefetch(track, int(m_range.group(1)) + sent)
                    return
            except requests.RequestException:
                # 尚未下发响应头前上游建连/首包失败（如 PCDN 超时、TLS 重置），切换备用源。
                self.server.report_url(track, url, False)
                if sent_headers:
                    return
                continue
            except (BrokenPipeError, ConnectionResetError, OSError):
                # 窗口关闭或拖动进度时，已断开的客户端不再接收数据；若尚未发头则尝试备用源。
                if sent_headers:
                    return
                self.server.report_url(track, url, False)
                continue
        self.send_error(502, "Upstream unavailable")
