# -*- coding: utf-8 -*-
"""下载及预览共用的媒体请求头。"""

from .domain import referer_for

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Referer": "https://www.douyin.com/",
}


def headers_for(url: str) -> dict:
    """按目标域名设置 Referer，每次返回独立字典。"""
    headers = dict(HEADERS)
    headers["Referer"] = referer_for(url)
    return headers
