# -*- coding: utf-8 -*-
"""抖音纯 Python 签名与高速直连模块（移植自 Apache-2.0 开源方案）。"""
from .client import fetch_aweme_detail_http, fetch_user_posts_http

__all__ = ["fetch_aweme_detail_http", "fetch_user_posts_http"]
