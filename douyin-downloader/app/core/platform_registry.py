# -*- coding: utf-8 -*-
"""站点解析调用契约与界面所需的能力声明。

这里只记录 sites.py 中的解析入口名称、SiteContext 参数和站点能力，不保存函数对象，
使每次解析仍能使用运行期替换的解析器（包括测试的 monkeypatch）。
域名与安全匹配仍由 domain.py 负责。
"""
from dataclasses import dataclass

from .domain import platform_of_source


@dataclass(frozen=True)
class PlatformRoute:
    parser_name: str
    context_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlatformCapabilities:
    """界面需要知道的站点差异，避免在各页面里写死站名。"""
    comic: bool = False        # 按「本子 / 章节」组织，预览走漫画阅读器
    open_label: str = "分享"   # 统计第三格的标签（有的平台给的是播放量）


DEFAULT_CAPABILITIES = PlatformCapabilities()


PLATFORM_ROUTES = {
    "douyin": PlatformRoute("_parse_douyin", ("cookie", "dy_profile_dir")),
    "x": PlatformRoute("parse_x", ("x_cookie", "x_profile_dir")),
    "instagram": PlatformRoute("parse_instagram", ("ins_cookie", "ins_profile_dir")),
    "bilibili": PlatformRoute("parse_bilibili", ("bili_cookie", "base_dir")),
    "xiaohongshu": PlatformRoute("parse_xiaohongshu", ("xhs_cookie", "xhs_profile_dir")),
    "jmcomic": PlatformRoute("_parse_jmcomic", ("jm_cookie",)),
    "pornhub": PlatformRoute("parse_pornhub"),
    "hanime1": PlatformRoute("parse_hanime1"),
    "youtube": PlatformRoute("parse_youtube", ("yt_cookie", "yt_profile_dir")),
}


PLATFORM_CAPABILITIES = {
    "douyin": PlatformCapabilities(),
    "x": PlatformCapabilities(),
    "instagram": PlatformCapabilities(),
    "bilibili": PlatformCapabilities(),
    "xiaohongshu": PlatformCapabilities(),
    "jmcomic": PlatformCapabilities(comic=True),
    "pornhub": PlatformCapabilities(),
    "hanime1": PlatformCapabilities(),
    "youtube": PlatformCapabilities(open_label="观看"),
}


def capabilities_for(source: str) -> PlatformCapabilities:
    """按 VideoInfo.source 取站点能力；未知来源退回默认能力。"""
    return PLATFORM_CAPABILITIES.get(platform_of_source(source), DEFAULT_CAPABILITIES)


def is_comic(source: str) -> bool:
    """该来源的作品是否按「本子 / 章节」组织。"""
    return capabilities_for(source).comic


def open_label_for(source: str) -> str:
    """统计第三位该叫「分享」还是「观看」。"""
    return capabilities_for(source).open_label
