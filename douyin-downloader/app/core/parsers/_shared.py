# -*- coding: utf-8 -*-
"""解析器跨站点共享小工具。"""


def _find_key_recursive(node, key, depth: int = 0):
    """在嵌套 JSON 中递归查找某个键的值"""
    if depth > 14:
        return None
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for v in node.values():
            found = _find_key_recursive(v, key, depth + 1)
            if found is not None:
                return found
    elif isinstance(node, list):
        for v in node:
            found = _find_key_recursive(v, key, depth + 1)
            if found is not None:
                return found
    return None
