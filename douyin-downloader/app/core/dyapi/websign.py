# -*- coding: utf-8 -*-
# ============================================================
# 声明 (Declaration)
#
# 本文件移植自以下开源项目的 WebSign 实现：
#   https://github.com/Evil0ctal/Douyin_TikTok_Download_API
#   src/dtk/signing/native/websign.py
#
# Portions Copyright (c) Evil0ctal
# Apache License 2.0: https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/LICENSE
# ============================================================
from __future__ import annotations

import hashlib
import time
from collections.abc import Iterable, Mapping, Sequence
from urllib.parse import quote, unquote

__all__ = [
    "EXPIRE_HEADER",
    "SALT",
    "SIGNATURE_PARAM",
    "TIMESTAMP_PARAM",
    "UIFID_COOKIE_NAMES",
    "UIFID_PARAM",
    "VERIFY_FP_COOKIE",
    "VERIFY_FP_PARAMS",
    "encode_pairs",
    "normalize_query",
    "pick_uifid",
    "sign",
]

SALT = "A96D855A08C0A9707F8BEF0D9A527E4E"

UIFID_COOKIE_NAMES: tuple[str, ...] = (
    "uifid",
    "uifid_temp",
    "uifidtemp",
    "UIFID",
    "UIFID_TEMP",
    "UIFIDTEMP",
)

VERIFY_FP_COOKIE = "s_v_web_id"
SIGNATURE_PARAM = "x-secsdk-web-signature"
UIFID_PARAM = "uifid"
TIMESTAMP_PARAM = "timestamp"
VERIFY_FP_PARAMS: tuple[str, ...] = ("verifyFp", "fp")
EXPIRE_HEADER = "x-secsdk-web-expire"


def pick_uifid(cookies: Mapping[str, str] | None) -> str | None:
    for name in UIFID_COOKIE_NAMES:
        value = (cookies or {}).get(name)
        if value:
            return value
    return None


def encode_pairs(pairs: Iterable[tuple[str, str]]) -> str:
    return "&".join(f"{quote(k, safe='*-._')}={quote(v, safe='*-._')}" for k, v in pairs)


def normalize_query(query: str) -> str:
    pairs: list[tuple[str, str]] = []
    for part in query.split("&"):
        if not part:
            continue
        name, _, value = part.partition("=")
        pairs.append((unquote(name), unquote(value)))
    return encode_pairs(pairs)


def sign(
    pairs: Sequence[tuple[str, str]],
    uifid: str,
    *,
    timestamp: int | None = None,
) -> tuple[str, str, dict[str, str]]:
    stamp = str(int(time.time() if timestamp is None else timestamp))
    covered = list(pairs)
    if not any(name == UIFID_PARAM for name, _ in covered):
        covered.append((UIFID_PARAM, uifid))
    covered.append((TIMESTAMP_PARAM, stamp))
    query = encode_pairs(covered)
    signature = hashlib.md5(f"{uifid}_{stamp}_{SALT}_{query}".encode()).hexdigest()
    headers = {
        UIFID_PARAM: uifid,
        SIGNATURE_PARAM: signature,
        EXPIRE_HEADER: stamp,
    }
    return f"{query}&{SIGNATURE_PARAM}={signature}", signature, headers
