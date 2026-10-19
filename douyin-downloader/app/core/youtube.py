# -*- coding: utf-8 -*-
"""YouTube 单视频解析（用 yt-dlp 提取格式，本程序负责下载）

为什么不在本模块里自己请求 watch 页面：
- YouTube 的播放地址要先按 player JS 解 nsig（签名变换），2025 年起还要过
  JS 挑战；规则随改版变化，自己实现等于长期维护一份逆向代码。
- yt-dlp 是纯 Python，可随包分发，只把它当**解析器**用：拿到 formats 后
  由本项目的 downloader 负责下载、断点续传、IDM 式多连接与 ffmpeg 合并。

档位规则（只挑 mp4 容器能承载的编码）：
- 1080p 及以下 YouTube 同时给 H.264（avc1）与 AV1，取 H.264 —— 兼容性最好；
- 1440p / 2160p 只有 AV1（mp4）与 VP9（webm），取 AV1：能装进 mp4，
  且下载器统一输出 .mp4，选 VP9 会因容器不匹配导致合并失败；
- 视频与音频分离的 DASH 流会给每一档配好 m4a 音轨（audio_map），
  下载时由 ffmpeg 合并。
"""
import os
import sys
from typing import Dict, List, Optional

from .models import VideoInfo
from .parser import ParseError

# 编码偏好：数值越大越优先。只看 mp4 容器里会出现的编码。
_CODECS = (("avc1", 3), ("av01", 2), ("vp09", 1), ("vp9", 1))
_CODEC_NAME = {"avc1": "H.264", "av01": "AV1", "vp09": "VP9", "vp9": "VP9"}
_VIDEO_EXTS = ("mp4",)
_AUDIO_EXTS = ("m4a", "mp4")


# --------------------------------------------------------------------- #
# 平台/代理/运行时定位
# --------------------------------------------------------------------- #
def _proxy_url() -> str:
    """取系统代理（YouTube 在境内通常需要），读不到就直连"""
    try:
        from .proxy import system_proxies
        proxies = system_proxies() or {}
    except Exception:  # noqa: BLE001 - 读不到代理按直连处理
        return ""
    return proxies.get("https") or proxies.get("http") or ""


def _node_path() -> str:
    """定位 node.exe：yt-dlp 解 YouTube 签名/JS 挑战要用（EJS 需要 node ≥ 20）"""
    # 1) 打包运行：onefile 解压目录 / exe 同目录
    if getattr(sys, "frozen", False):
        roots = [getattr(sys, "_MEIPASS", "") or "", os.path.dirname(sys.executable)]
        for root in roots:
            for rel in (os.path.join("playwright", "driver", "node.exe"), "node.exe"):
                cand = os.path.join(root, rel)
                if os.path.isfile(cand):
                    return cand
    # 2) 开发期：Playwright 自带的 node（版本新，EJS 要求 ≥ 20）
    try:
        import playwright
        cand = os.path.join(os.path.dirname(playwright.__file__),
                            "driver", "node.exe")
        if os.path.isfile(cand):
            return cand
    except Exception:  # noqa: BLE001 - 没装 playwright 就走下一层
        pass
    # 3) 复用小红书签名那套定位（exe 同目录 / 项目根 / 系统 PATH）
    try:
        from .xhsapi._node import get_node_cmd
        return get_node_cmd()
    except Exception:  # noqa: BLE001 - 找不到就不传 runtime，由 yt-dlp 自行降级
        return ""


class _QuietLogger:
    """yt-dlp 日志收进列表：界面不显示，失败时留给调用方判因"""

    def __init__(self):
        self.messages: List[str] = []

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        self.messages.append(str(msg))

    def error(self, msg):
        self.messages.append(str(msg))


# --------------------------------------------------------------------- #
# 格式挑选（纯函数，便于单测）
# --------------------------------------------------------------------- #
def _size(fmt: dict) -> int:
    try:
        return int(fmt.get("filesize") or fmt.get("filesize_approx") or 0)
    except (TypeError, ValueError):
        return 0


def _height(fmt: dict) -> int:
    try:
        return int(fmt.get("height") or 0)
    except (TypeError, ValueError):
        return 0


def _fps(fmt: dict) -> int:
    try:
        return int(round(float(fmt.get("fps") or 0)))
    except (TypeError, ValueError):
        return 0


def _abr(fmt: dict) -> float:
    try:
        return float(fmt.get("abr") or 0)
    except (TypeError, ValueError):
        return 0.0


def _codec_of(fmt: dict) -> str:
    codec = str(fmt.get("vcodec") or "").lower()
    for name, _ in _CODECS:
        if codec.startswith(name):
            return name
    return ""


def _codec_rank(fmt: dict) -> int:
    codec = _codec_of(fmt)
    for name, rank in _CODECS:
        if name == codec:
            return rank
    return 0


def _is_direct(fmt: dict) -> bool:
    """是不是可直接 GET 的 http(s) 直链（排除 HLS/DASH 清单、DRM、分段协议）"""
    url = str(fmt.get("url") or "")
    if not url:
        return False
    if not str(fmt.get("protocol") or "").lower().startswith("http"):
        return False
    if fmt.get("has_drm"):
        return False
    # 清单（.m3u8/.mpd）不是单个文件，下回来的是文本；正常 yt-dlp 标
    # m3u8_native 已被前缀判断挡掉，这里再按后缀兜一层
    path = url.split("?", 1)[0].split("#", 1)[0].lower()
    if path.endswith((".m3u8", ".mpd")):
        return False
    return True


def _label(fmt: dict) -> str:
    """画质标签：2160p · 60fps · AV1（H.264 是默认编码，不标）"""
    height = _height(fmt)
    label = f"{height}p" if height else "视频"
    fps = _fps(fmt)
    if fps:
        label += f" · {fps}fps"
    codec = _codec_of(fmt)
    if codec and codec != "avc1":
        label += f" · {_CODEC_NAME.get(codec, codec)}"
    return label


def _best_by_height(formats: List[dict]) -> List[dict]:
    """同一高度只留最优一条（先帧率，再编码），按画质从高到低排序"""
    best: Dict[int, dict] = {}
    for fmt in formats:
        height = _height(fmt)
        if height <= 0:
            continue
        cur = best.get(height)
        if cur is None or ((_fps(fmt), _codec_rank(fmt))
                           > (_fps(cur), _codec_rank(cur))):
            best[height] = fmt
    return sorted(best.values(),
                  key=lambda f: (_height(f), _fps(f), _codec_rank(f)),
                  reverse=True)


def select_formats(formats) -> List[dict]:
    """从 yt-dlp formats 里挑出可下载档位

    返回 [{"label", "url", "size", "audio"}]，按画质从高到低：
    - DASH 档位（视频/音频分离）会带上对应 m4a 音轨；
    - 没有可用 DASH 时回退到音视频一体的 mp4（YouTube 的 18/22 号格式）。
    """
    videos, singles, audios = [], [], []
    for fmt in formats or []:
        if not isinstance(fmt, dict) or not _is_direct(fmt):
            continue
        ext = str(fmt.get("ext") or "").lower()
        vcodec = str(fmt.get("vcodec") or "none")
        acodec = str(fmt.get("acodec") or "none")
        if vcodec != "none" and acodec == "none":
            if ext in _VIDEO_EXTS:
                videos.append(fmt)
        elif vcodec != "none" and acodec != "none":
            if ext in _VIDEO_EXTS:
                singles.append(fmt)
        elif vcodec == "none" and acodec != "none":
            if ext in _AUDIO_EXTS:
                audios.append(fmt)

    audio = max(audios, key=_abr) if audios else None
    picks: List[dict] = []
    if videos and audio is not None:
        picks = [{
            "label": _label(fmt),
            "url": str(fmt["url"]),
            "size": _size(fmt) + _size(audio),
            "audio": str(audio["url"]),
        } for fmt in _best_by_height(videos)]
    if not picks:
        # 没有可配对的音轨：退回音视频一体的 mp4，避免下出无声视频
        picks = [{
            "label": _label(fmt),
            "url": str(fmt["url"]),
            "size": _size(fmt),
            "audio": "",
        } for fmt in _best_by_height(singles)]
    return picks


def _thumb_size(thumb: dict) -> tuple:
    """封面尺寸（宽, 高）；yt-dlp 偶尔给出非数字字符串，按 0 处理"""
    try:
        return (int(thumb.get("width") or 0), int(thumb.get("height") or 0))
    except (TypeError, ValueError):
        return (0, 0)


def _best_thumbnail(data: dict) -> str:
    thumbs = [t for t in (data.get("thumbnails") or [])
              if isinstance(t, dict) and t.get("url")]
    if thumbs:
        return str(max(thumbs, key=_thumb_size)["url"])
    return str(data.get("thumbnail") or "")


# --------------------------------------------------------------------- #
# 解析入口
# --------------------------------------------------------------------- #
def build_video_info(data: dict) -> VideoInfo:
    """把 yt-dlp 的 info dict 转成 VideoInfo（不联网，便于单测）"""
    picks = select_formats(data.get("formats") or [])
    if not picks:
        raise ParseError("这个视频没有可直接下载的 MP4 格式"
                         "（可能只有 WebM，或需要登录/会员）")
    info = VideoInfo()
    info.source = "youtube"
    info.aweme_id = str(data.get("id") or "")
    info.title = (data.get("title") or "").strip() or info.aweme_id
    info.author = (data.get("uploader") or data.get("channel") or "").strip()
    info.author_uid = str(data.get("channel_id") or "")
    info.cover_url = _best_thumbnail(data)
    try:
        info.duration = int(float(data.get("duration") or 0) * 1000)
    except (TypeError, ValueError):
        info.duration = 0
    for src_key, attr in (("view_count", "share_count"),
                          ("like_count", "digg_count"),
                          ("comment_count", "comment_count")):
        try:
            setattr(info, attr, int(data.get(src_key) or 0))
        except (TypeError, ValueError):
            setattr(info, attr, 0)
    try:
        info.create_time = int(data.get("release_timestamp")
                               or data.get("timestamp") or 0)
    except (TypeError, ValueError):
        info.create_time = 0

    info.quality_options = [(p["label"], p["url"]) for p in picks]
    info.quality_sizes = {p["url"]: p["size"] for p in picks if p["size"]}
    info.audio_map = {p["url"]: p["audio"] for p in picks if p["audio"]}
    info.play_url = picks[0]["url"]
    info.quality = picks[0]["label"]
    info.play_url_candidates = [p["url"] for p in picks]
    info.raw_play_url = info.play_url
    return info


_ERROR_HINTS = (
    ("sign in to confirm your age", "该视频有年龄限制，需要登录 YouTube 账号后才能解析"),
    ("confirm you're not a bot", "YouTube 要求人机验证，请稍后重试或更换网络"),
    ("members-only", "该视频仅频道会员可看，无法解析"),
    ("private video", "该视频是私享视频，无法解析"),
    ("this video is unavailable", "视频不可用（可能已删除、被地区限制，或链接有误）"),
    ("video unavailable", "视频不可用（可能已删除、被地区限制，或链接有误）"),
    ("this live event has ended", "这场直播已经结束"),
    ("premieres in", "该视频还没开播（首播预告）"),
    ("live event will begin", "该视频还没开播（直播预告）"),
    ("unable to download webpage", "无法访问 YouTube，请确认网络或代理已开启"),
    ("timed out", "访问 YouTube 超时，请确认网络或代理已开启"),
    ("signature solving failed", "YouTube 签名解析失败，请更新 yt-dlp 解析组件"),
    ("nsig", "YouTube 签名解析失败，请更新 yt-dlp 解析组件"),
)


def _friendly_error(exc: Exception) -> str:
    text = str(exc)
    low = text.lower()
    for key, hint in _ERROR_HINTS:
        if key in low:
            return hint
    return f"YouTube 解析失败：{text[:200]}"


def _ydl_options(timeout: int) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "skip_download": True,       # 只取格式，下载交给本项目 downloader
        "noplaylist": True,          # 带 list= 参数的单个视频只解析视频本身
        "socket_timeout": max(5, int(timeout or 15)),
        "retries": 2,
        "extractor_retries": 2,
        "logger": _QuietLogger(),
    }
    proxy = _proxy_url()
    if proxy:
        opts["proxy"] = proxy
    node = _node_path()
    # 显式给 node：不给时 yt-dlp 只认 deno，签名会解不出来（格式会缺档）
    opts["js_runtimes"] = {"node": {"path": node}} if node else {}
    return opts


def _reject_unsupported(url: str) -> None:
    """明显不是单个视频的页面先拦掉，免得 yt-dlp 去展开整个频道/歌单（很慢）"""
    import urllib.parse as _urlparse
    path = (_urlparse.urlparse(url or "").path or "").lower()
    if path.startswith(("/playlist", "/results")):
        raise ParseError("这是播放列表/搜索结果链接，目前只支持单个视频。"
                         "请打开视频后复制它自己的链接")
    if path.startswith(("/@", "/channel/", "/user/", "/c/")):
        raise ParseError("这是频道/主页链接，目前只支持单个视频。"
                         "请打开视频后复制它自己的链接")


def parse_youtube(url: str, timeout: int = 30) -> VideoInfo:
    """解析一个 YouTube 视频链接（watch / youtu.be / shorts / live 均可）"""
    _reject_unsupported(url)
    try:
        from yt_dlp import YoutubeDL
        from yt_dlp.utils import DownloadError
    except ImportError as e:  # 打包缺组件时给出可读提示
        raise ParseError("缺少 YouTube 解析组件（yt-dlp），请安装完整版本后再试") from e

    try:
        with YoutubeDL(_ydl_options(timeout)) as ydl:
            data = ydl.extract_info(url, download=False)
    except DownloadError as e:
        raise ParseError(_friendly_error(e)) from e
    except Exception as e:  # noqa: BLE001 - yt-dlp 内部异常类型很杂
        raise ParseError(f"YouTube 解析失败：{e}") from e

    if not isinstance(data, dict):
        raise ParseError("YouTube 没有返回视频信息，请确认链接有效")
    if data.get("_type") == "playlist":
        raise ParseError("这是播放列表/频道链接，目前只支持单个视频。"
                         "请打开视频后复制它自己的链接")
    if data.get("is_live"):
        raise ParseError("暂不支持 YouTube 直播，请等直播结束或换成普通视频链接")
    if data.get("live_status") in ("is_upcoming", "is_live"):
        raise ParseError("该视频还没开播（直播预告），暂时无法解析")
    return build_video_info(data)
