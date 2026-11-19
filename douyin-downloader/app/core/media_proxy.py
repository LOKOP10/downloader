# -*- coding: utf-8 -*-
"""预览媒体的本地代理：转发请求头、字节区间与备用源。"""

import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

from .domain import needs_cookie_for_url
from .http_headers import headers_for
from .models import clean_cookie


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
        self.thread = threading.Thread(target=self.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def url(self, track):
        """返回供播放器使用的本地地址。"""
        return f"http://127.0.0.1:{self.server_port}/{self.token}/{track}"

    def stop(self):
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=1)


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
        urls = self.server.tracks[parts[2]]
        byte_range = self.headers.get("Range", "")
        for url in urls:
            try:
                with requests.request("HEAD" if head_only else "GET", url,
                                      headers=media_headers(url, self.server.cookies, byte_range),
                                      stream=True, timeout=(8, 20)) as upstream:
                    if upstream.status_code not in (200, 206):
                        continue
                    if byte_range and upstream.status_code != 206:
                        continue
                    self.send_response(upstream.status_code)
                    for name in ("Content-Type", "Content-Length", "Content-Range",
                                 "Accept-Ranges"):
                        value = upstream.headers.get(name)
                        if value:
                            self.send_header(name, value)
                    self.send_header("Connection", "close")
                    self.end_headers()
                    if not head_only:
                        for chunk in upstream.iter_content(64 * 1024):
                            if chunk:
                                self.wfile.write(chunk)
                    return
            except (requests.RequestException, BrokenPipeError, ConnectionResetError, OSError):
                # 窗口关闭或拖动进度时，已断开的客户端不再接收数据。
                return
        self.send_error(502, "媒体源不可用")
