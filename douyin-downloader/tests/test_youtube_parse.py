# -*- coding: utf-8 -*-
"""YouTube 解析单元测试（域名识别 / 档位挑选 / 字段映射 / 错误文案 / 站点分发）

覆盖三块纯逻辑，全程不联网：
  - domain：YOUTUBE_HOSTS 的标签边界匹配（m./music. 子域命中，仿冒域拒绝）与 Referer
  - youtube.select_formats：同高度 H.264 优先、60fps 优先、m4a 取最高 abr、
    size = 视频 + 音频、排除 webm/m3u8/DRM/无直链、无音轨回退单文件 mp4
  - youtube.build_video_info：字段映射（duration 秒转毫秒、统计映射、封面最宽）
  - youtube.parse_youtube：用 sys.modules 注入假 yt_dlp，验证播放列表/直播/
    年龄限制等错误文案与正常返回；DownloadError 用自定义异常模拟
  - sites._parse_share：YouTube 链接必须交给 parse_youtube 处理

注入的假 yt_dlp 通过 unittest.mock.patch.dict(sys.modules, ...) 安装，
退出 with 时自动还原，不会污染其它测试。
"""
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import sites  # noqa: E402
from app.core.domain import (  # noqa: E402
    needs_cookie_for_source,
    platform_of_host,
    platform_of_source,
    referer_for,
)
from app.core.models import VideoInfo  # noqa: E402
from app.core.parser import ParseError  # noqa: E402
from app.core.youtube import (  # noqa: E402
    build_video_info,
    parse_youtube,
    select_formats,
)

WATCH_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


# --------------------------------------------------------------------- #
# 构造 yt-dlp formats 条目
# --------------------------------------------------------------------- #
def make_format(url, *, ext="mp4", height=0, fps=30, vcodec="avc1.640028",
                acodec="none", filesize=0, abr=0, protocol="https", **extra):
    """构造一条 formats 记录（默认是只有视频轨的 DASH 档位）"""
    fmt = {
        "url": url,
        "ext": ext,
        "height": height,
        "fps": fps,
        "vcodec": vcodec,
        "acodec": acodec,
        "filesize": filesize,
        "protocol": protocol,
    }
    if abr:
        fmt["abr"] = abr
    fmt.update(extra)
    return fmt


def make_audio(url, *, abr=128, filesize=0, ext="m4a"):
    """构造一条只有音频轨的 m4a（YouTube 的 139/140 就是这种）"""
    return make_format(url, ext=ext, height=0, fps=0, vcodec="none",
                       acodec="mp4a.40.2", filesize=filesize, abr=abr)


def make_single(url, *, height=360, fps=30, filesize=0):
    """音视频一体的单文件 mp4（YouTube 的 18/22 号格式）"""
    return make_format(url, height=height, fps=fps, vcodec="avc1.42001E",
                       acodec="mp4a.40.2", filesize=filesize)


AUDIO_140 = make_audio("https://a/140.m4a", abr=128, filesize=3_000_000)


# --------------------------------------------------------------------- #
# 域名识别
# --------------------------------------------------------------------- #
class YoutubeHostTest(unittest.TestCase):
    def test_official_hosts_match(self):
        urls = (
            "https://www.youtube.com/watch?v=abc",
            "https://youtube.com/watch?v=abc",
            "https://m.youtube.com/watch?v=abc",
            "https://music.youtube.com/watch?v=abc",
            "https://youtu.be/abc123",
            "https://www.youtube-nocookie.com/embed/abc123",
            "https://googlevideo.com/videoplayback?x=1",
            "https://rr1---sn-abc.googlevideo.com/videoplayback?x=1",
        )
        for url in urls:
            self.assertEqual("youtube", platform_of_host(url), url)

    def test_lookalike_hosts_are_rejected(self):
        """按域名标签边界匹配：仿冒域不能命中，否则可能给钓鱼站发请求"""
        urls = (
            "https://notyoutube.com/watch?v=abc",
            "https://youtube.com.evil.cn/watch?v=abc",
            "https://youtu.be.evil.cn/abc",
            "https://googlevideo.com.evil.cn/videoplayback",
        )
        for url in urls:
            self.assertEqual("", platform_of_host(url), url)

    def test_source_alias_yt_normalizes_to_youtube(self):
        self.assertEqual("youtube", platform_of_source("yt"))
        self.assertEqual("youtube", platform_of_source("YouTube"))
        self.assertEqual("youtube", platform_of_source(" youtube "))

    def test_youtube_cdn_does_not_need_cookie(self):
        """googlevideo 直链带时效签名，不需要登录态 Cookie"""
        self.assertFalse(needs_cookie_for_source("youtube"))

    def test_referer_for_googlevideo(self):
        self.assertEqual(
            "https://www.youtube.com/",
            referer_for("https://rr1---sn-abc.googlevideo.com/videoplayback?x=1"))
        self.assertEqual("https://www.youtube.com/",
                         referer_for("https://youtu.be/abc123"))
        # 仿冒域走默认 Referer，不能把 YouTube Referer 发出去
        self.assertEqual("https://www.douyin.com/",
                         referer_for("https://notyoutube.com/x"))


# --------------------------------------------------------------------- #
# select_formats
# --------------------------------------------------------------------- #
class SelectFormatsTest(unittest.TestCase):
    def test_prefers_avc1_over_av01_at_same_height(self):
        """1080p 同时有 H.264 与 AV1 时取 H.264（兼容性最好）"""
        avc1 = make_format("https://v/1080-avc1.mp4", height=1080, fps=30,
                           vcodec="avc1.640028", filesize=50_000_000)
        av01 = make_format("https://v/1080-av01.mp4", height=1080, fps=30,
                           vcodec="av01.0.08M", filesize=40_000_000)
        picks = select_formats([av01, avc1, AUDIO_140])
        self.assertEqual(1, len(picks))
        self.assertEqual("https://v/1080-avc1.mp4", picks[0]["url"])
        self.assertEqual("1080p · 30fps", picks[0]["label"])
        self.assertNotIn("AV1", picks[0]["label"])

    def test_2160p_av01_label_mentions_av1(self):
        """2160p 只有 AV1 可选，标签要标出编码"""
        fmt = make_format("https://v/2160-av01.mp4", height=2160, fps=60,
                          vcodec="av01.0.08M", filesize=200_000_000)
        picks = select_formats([fmt, AUDIO_140])
        self.assertEqual(1, len(picks))
        self.assertEqual("2160p · 60fps · AV1", picks[0]["label"])

    def test_60fps_wins_over_25fps_at_same_height(self):
        """同高度先比帧率：60fps 的 AV1 也要压过 25fps 的 H.264"""
        slow = make_format("https://v/1080-25fps.mp4", height=1080, fps=25,
                           vcodec="avc1.640028", filesize=30_000_000)
        fast = make_format("https://v/1080-60fps.mp4", height=1080, fps=60,
                           vcodec="av01.0.08M", filesize=40_000_000)
        picks = select_formats([slow, fast, AUDIO_140])
        self.assertEqual(1, len(picks))
        self.assertEqual("https://v/1080-60fps.mp4", picks[0]["url"])
        self.assertEqual("1080p · 60fps · AV1", picks[0]["label"])

    def test_picks_highest_abr_m4a(self):
        """音轨取 abr 最高的 140，不能拿 139"""
        video = make_format("https://v/1080.mp4", height=1080, fps=30,
                            filesize=50_000_000)
        a139 = make_audio("https://a/139.m4a", abr=48, filesize=1_000_000)
        a140 = make_audio("https://a/140.m4a", abr=128, filesize=3_000_000)
        picks = select_formats([video, a139, a140])
        self.assertEqual(1, len(picks))
        self.assertEqual("https://a/140.m4a", picks[0]["audio"])

    def test_size_is_video_plus_audio(self):
        video = make_format("https://v/1080.mp4", height=1080, fps=30,
                            filesize=50_000_000)
        audio = make_audio("https://a/140.m4a", abr=128, filesize=3_000_000)
        picks = select_formats([video, audio])
        self.assertEqual(1, len(picks))
        self.assertEqual(53_000_000, picks[0]["size"])

    def test_sorted_from_high_to_low(self):
        formats = [
            make_format("https://v/720.mp4", height=720, fps=30, filesize=20),
            make_format("https://v/2160.mp4", height=2160, fps=60,
                        vcodec="av01.0.08M", filesize=200),
            make_format("https://v/1080.mp4", height=1080, fps=30, filesize=50),
            AUDIO_140,
        ]
        picks = select_formats(formats)
        self.assertEqual(
            ["2160p · 60fps · AV1", "1080p · 30fps", "720p · 30fps"],
            [p["label"] for p in picks])
        self.assertEqual(
            ["https://v/2160.mp4", "https://v/1080.mp4", "https://v/720.mp4"],
            [p["url"] for p in picks])

    def test_excludes_webm(self):
        """webm 装不进本项目统一输出的 mp4，必须排除"""
        webm = make_format("https://v/1080.webm", ext="webm", height=1080, fps=30,
                           vcodec="vp9", filesize=40_000_000)
        self.assertEqual([], select_formats([webm, AUDIO_140]))

    def test_excludes_m3u8_manifest(self):
        """m3u8 是清单不是直链，交给下载器会失败"""
        hls = make_format("https://v/playlist.m3u8", height=1080, fps=30,
                          protocol="m3u8_native", filesize=40_000_000)
        self.assertEqual([], select_formats([hls, AUDIO_140]))

    def test_excludes_drm(self):
        drm = make_format("https://v/1080-drm.mp4", height=1080, fps=30,
                          filesize=40_000_000, has_drm=True)
        self.assertEqual([], select_formats([drm, AUDIO_140]))

    def test_excludes_format_without_url(self):
        no_url = make_format("", height=1080, fps=30, filesize=40_000_000)
        self.assertEqual([], select_formats([no_url, AUDIO_140]))

    def test_excludes_non_http_protocol(self):
        weird = make_format("https://v/1080.mp4", height=1080, fps=30,
                            protocol="rtmp", filesize=40_000_000)
        self.assertEqual([], select_formats([weird, AUDIO_140]))

    def test_falls_back_to_single_file_mp4_without_dash_audio(self):
        """没有 DASH 音轨时退回音视频一体的 mp4（避免下出无声视频）"""
        single = make_single("https://v/18.mp4", height=360, fps=30,
                             filesize=30_000_000)
        picks = select_formats([single])
        self.assertEqual(1, len(picks))
        self.assertEqual("https://v/18.mp4", picks[0]["url"])
        self.assertEqual("360p · 30fps", picks[0]["label"])
        self.assertEqual(30_000_000, picks[0]["size"])
        self.assertEqual("", picks[0]["audio"])

    def test_falls_back_to_single_when_dash_has_no_audio(self):
        """有视频轨但缺音频轨时，也回退到单文件 mp4"""
        video = make_format("https://v/1080.mp4", height=1080, fps=30,
                            filesize=50_000_000)
        single = make_single("https://v/18.mp4", height=360, fps=30,
                             filesize=30_000_000)
        picks = select_formats([video, single])
        self.assertEqual(["https://v/18.mp4"], [p["url"] for p in picks])

    def test_audio_only_returns_empty(self):
        self.assertEqual([], select_formats([AUDIO_140]))

    def test_no_usable_formats_returns_empty(self):
        for formats in ([], None, [None], ["not-a-dict"], [{}]):
            self.assertEqual([], select_formats(formats), repr(formats))


# --------------------------------------------------------------------- #
# build_video_info
# --------------------------------------------------------------------- #
class BuildVideoInfoTest(unittest.TestCase):
    def _data(self, **overrides):
        data = {
            "id": "dQw4w9WgXcQ",
            "title": "  Never Gonna Give You Up  ",
            "uploader": "Rick Astley",
            "channel_id": "UCuAXFkgsw1L7xaCfnd5JJOw",
            "duration": 212.5,
            "view_count": 1_500_000,
            "like_count": 15_000,
            "comment_count": 2_000,
            "thumbnails": [
                {"url": "https://i/small.jpg", "width": 320, "height": 180},
                {"url": "https://i/wide.jpg", "width": 1280, "height": 720},
                {"url": "https://i/tall.jpg", "width": 1280, "height": 1000},
            ],
            "thumbnail": "https://i/default.jpg",
            "formats": [
                make_format("https://v/1080.mp4", height=1080, fps=30,
                            filesize=50_000_000),
                make_format("https://v/2160.mp4", height=2160, fps=60,
                            vcodec="av01.0.08M", filesize=200_000_000),
                AUDIO_140,
            ],
        }
        data.update(overrides)
        return data

    def test_maps_basic_fields(self):
        info = build_video_info(self._data())
        self.assertIsInstance(info, VideoInfo)
        self.assertEqual("youtube", info.source)
        self.assertEqual("dQw4w9WgXcQ", info.aweme_id)
        self.assertEqual("Never Gonna Give You Up", info.title)
        self.assertEqual("Rick Astley", info.author)
        self.assertEqual("UCuAXFkgsw1L7xaCfnd5JJOw", info.author_uid)
        self.assertFalse(info.is_image)

    def test_duration_seconds_to_milliseconds(self):
        self.assertEqual(212_500, build_video_info(self._data()).duration)

    def test_duration_invalid_is_zero(self):
        for bad in ("abc", None, 0, ""):
            info = build_video_info(self._data(duration=bad))
            self.assertEqual(0, info.duration, repr(bad))

    def test_stats_mapping(self):
        info = build_video_info(self._data())
        self.assertEqual(1_500_000, info.share_count)   # view_count
        self.assertEqual(15_000, info.digg_count)       # like_count
        self.assertEqual(2_000, info.comment_count)

    def test_stats_invalid_is_zero(self):
        info = build_video_info(self._data(view_count="x", like_count=None,
                                           comment_count=""))
        self.assertEqual(0, info.share_count)
        self.assertEqual(0, info.digg_count)
        self.assertEqual(0, info.comment_count)

    def test_cover_picks_widest_then_tallest(self):
        """封面取 thumbnails 里最宽的；同宽时取更高的那一张"""
        info = build_video_info(self._data())
        self.assertEqual("https://i/tall.jpg", info.cover_url)

    def test_cover_falls_back_to_thumbnail_field(self):
        info = build_video_info(self._data(thumbnails=[]))
        self.assertEqual("https://i/default.jpg", info.cover_url)

    def test_cover_without_any_thumbnail_is_empty(self):
        info = build_video_info(self._data(thumbnails=[], thumbnail=""))
        self.assertEqual("", info.cover_url)

    def test_quality_options_sorted_desc(self):
        info = build_video_info(self._data())
        self.assertEqual(
            [("2160p · 60fps · AV1", "https://v/2160.mp4"),
             ("1080p · 30fps", "https://v/1080.mp4")],
            info.quality_options)

    def test_play_url_is_highest_quality(self):
        info = build_video_info(self._data())
        self.assertEqual("https://v/2160.mp4", info.play_url)
        self.assertEqual("https://v/2160.mp4", info.raw_play_url)
        self.assertEqual("2160p · 60fps · AV1", info.quality)
        self.assertEqual(["https://v/2160.mp4", "https://v/1080.mp4"],
                         info.play_url_candidates)

    def test_audio_map_covers_every_dash_tier(self):
        info = build_video_info(self._data())
        self.assertEqual({
            "https://v/2160.mp4": "https://a/140.m4a",
            "https://v/1080.mp4": "https://a/140.m4a",
        }, info.audio_map)

    def test_quality_sizes_include_video_plus_audio(self):
        info = build_video_info(self._data())
        self.assertEqual(203_000_000, info.quality_sizes["https://v/2160.mp4"])
        self.assertEqual(53_000_000, info.quality_sizes["https://v/1080.mp4"])

    def test_quality_sizes_skip_unknown_size(self):
        """拿不到大小的档位不进 quality_sizes，但仍在 quality_options 里"""
        formats = [
            make_format("https://v/1080.mp4", height=1080, fps=30,
                        filesize=50_000_000),
            make_format("https://v/720.mp4", height=720, fps=30, filesize=0),
            make_audio("https://a/140.m4a", abr=128, filesize=0),
        ]
        info = build_video_info(self._data(formats=formats))
        self.assertEqual(["https://v/1080.mp4", "https://v/720.mp4"],
                         [u for _, u in info.quality_options])
        self.assertEqual({"https://v/1080.mp4": 50_000_000},
                         info.quality_sizes)

    def test_single_file_has_no_audio_map(self):
        formats = [make_single("https://v/18.mp4", height=360, fps=30,
                               filesize=30_000_000)]
        info = build_video_info(self._data(formats=formats))
        self.assertEqual({}, info.audio_map)
        self.assertEqual("https://v/18.mp4", info.play_url)
        self.assertEqual(30_000_000, info.quality_sizes["https://v/18.mp4"])

    def test_empty_formats_raises_parse_error(self):
        for formats in ([], None, [make_format("https://v/1080.webm",
                                               ext="webm", height=1080)]):
            with self.assertRaises(ParseError):
                build_video_info(self._data(formats=formats))

    def test_empty_title_falls_back_to_video_id(self):
        info = build_video_info(self._data(title="   "))
        self.assertEqual("dQw4w9WgXcQ", info.title)


# --------------------------------------------------------------------- #
# parse_youtube：注入假 yt_dlp（不联网）
# --------------------------------------------------------------------- #
class _FakeDownloadError(Exception):
    """对应 yt_dlp.utils.DownloadError"""


def fake_yt_dlp(info=None, error=None):
    """构造假 yt_dlp 模块；返回 (待注入 sys.modules 的字典, 调用记录)"""
    module = types.ModuleType("yt_dlp")
    utils = types.ModuleType("yt_dlp.utils")
    utils.DownloadError = _FakeDownloadError
    calls = {}

    class FakeYoutubeDL:
        def __init__(self, options=None):
            calls["options"] = options

        def __enter__(self):
            calls["entered"] = True
            return self

        def __exit__(self, exc_type, exc, tb):
            calls["exited"] = True
            return False

        def extract_info(self, url, download=True):
            calls["url"] = url
            calls["download"] = download
            if error is not None:
                raise error
            return info

    module.YoutubeDL = FakeYoutubeDL
    module.utils = utils
    return {"yt_dlp": module, "yt_dlp.utils": utils}, calls


class ParseYoutubeTest(unittest.TestCase):
    def _parse_ok(self, info, url=WATCH_URL):
        mods, calls = fake_yt_dlp(info=info)
        # 只隔离代理/Node 探测，待测的解析流程照常执行
        with patch.dict(sys.modules, mods), \
                patch("app.core.youtube._proxy_url", return_value=""), \
                patch("app.core.youtube._node_path", return_value=""):
            result = parse_youtube(url)
        return result, calls

    def _parse_fail(self, error=None, info=None, url=WATCH_URL):
        mods, calls = fake_yt_dlp(info=info, error=error)
        with patch.dict(sys.modules, mods), \
                patch("app.core.youtube._proxy_url", return_value=""), \
                patch("app.core.youtube._node_path", return_value=""):
            with self.assertRaises(ParseError) as ctx:
                parse_youtube(url)
        return str(ctx.exception), calls

    def _sample_info(self):
        return {
            "id": "dQw4w9WgXcQ",
            "title": "Never Gonna Give You Up",
            "uploader": "Rick Astley",
            "channel_id": "UCuAXFkgsw1L7xaCfnd5JJOw",
            "duration": 212.5,
            "view_count": 100,
            "like_count": 20,
            "comment_count": 3,
            "thumbnails": [{"url": "https://i/cover.jpg",
                            "width": 1280, "height": 720}],
            "formats": [
                make_format("https://v/1080.mp4", height=1080, fps=30,
                            filesize=50_000_000),
                AUDIO_140,
            ],
        }

    def test_normal_path_returns_video_info(self):
        result, calls = self._parse_ok(self._sample_info())
        self.assertIsInstance(result, VideoInfo)
        self.assertEqual("youtube", result.source)
        self.assertEqual("dQw4w9WgXcQ", result.aweme_id)
        self.assertEqual("https://v/1080.mp4", result.play_url)
        self.assertEqual({"https://v/1080.mp4": "https://a/140.m4a"},
                         result.audio_map)
        self.assertEqual(212_500, result.duration)
        # 只解析不下载；上下文管理器要正确进入/退出
        self.assertEqual(WATCH_URL, calls["url"])
        self.assertIs(False, calls["download"])
        self.assertTrue(calls.get("entered"))
        self.assertTrue(calls.get("exited"))
        self.assertIs(True, calls["options"]["skip_download"])

    def test_short_and_live_paths_are_not_rejected_early(self):
        """shorts / live / youtu.be 短链不能被提前拦截误伤"""
        for url in ("https://www.youtube.com/shorts/abc",
                    "https://www.youtube.com/live/abc",
                    "https://youtu.be/abc"):
            with self.subTest(url=url):
                _, calls = self._parse_ok(self._sample_info(), url=url)
                self.assertEqual(url, calls["url"])

    def test_playlist_and_channel_paths_are_rejected_early(self):
        """明显不是单视频的页面在进 yt-dlp 之前就拦掉（否则会展开整个歌单/频道）"""
        cases = (
            ("https://www.youtube.com/playlist?list=PL123", "播放列表"),
            ("https://www.youtube.com/results?search_query=x", "播放列表"),
            ("https://www.youtube.com/@somebody", "频道"),
            ("https://www.youtube.com/channel/UC123", "频道"),
            ("https://www.youtube.com/user/foo", "频道"),
            ("https://www.youtube.com/c/foo", "频道"),
        )
        for url, keyword in cases:
            with self.subTest(url=url):
                text, calls = self._parse_fail(info={}, url=url)
                self.assertIn(keyword, text)
                self.assertNotIn("url", calls)  # 没有进 yt-dlp

    def test_playlist_is_rejected(self):
        text, _ = self._parse_fail(info={"_type": "playlist",
                                          "title": "Some playlist"})
        self.assertIn("播放列表", text)

    def test_live_video_is_rejected(self):
        text, _ = self._parse_fail(info={"id": "x", "is_live": True})
        self.assertIn("直播", text)

    def test_upcoming_live_is_rejected(self):
        text, _ = self._parse_fail(info={"id": "x",
                                          "live_status": "is_upcoming"})
        self.assertIn("还没开播", text)

    def test_live_status_is_live_is_rejected(self):
        text, _ = self._parse_fail(info={"id": "x",
                                          "live_status": "is_live"})
        self.assertIn("还没开播", text)

    def test_age_restricted_error_is_friendly(self):
        err = _FakeDownloadError(
            "ERROR: Sign in to confirm your age. This video may be "
            "inappropriate for some users.")
        text, _ = self._parse_fail(error=err)
        self.assertIn("年龄限制", text)

    def test_members_only_error_is_friendly(self):
        err = _FakeDownloadError("Join this channel to get access: "
                                 "members-only content")
        text, _ = self._parse_fail(error=err)
        self.assertIn("会员", text)

    def test_private_video_error_is_friendly(self):
        err = _FakeDownloadError("Private video. Sign in if you've been "
                                 "granted access to this video")
        text, _ = self._parse_fail(error=err)
        self.assertIn("私享视频", text)

    def test_video_unavailable_error_is_friendly(self):
        err = _FakeDownloadError("Video unavailable")
        text, _ = self._parse_fail(error=err)
        self.assertIn("视频不可用", text)

    def test_network_timeout_error_is_friendly(self):
        err = _FakeDownloadError("The read operation timed out")
        text, _ = self._parse_fail(error=err)
        self.assertIn("超时", text)

    def test_signature_error_is_friendly(self):
        err = _FakeDownloadError("Signature solving failed: some challenge")
        text, _ = self._parse_fail(error=err)
        self.assertIn("签名解析失败", text)

    def test_unknown_download_error_keeps_original_text(self):
        err = _FakeDownloadError("boom 42")
        text, _ = self._parse_fail(error=err)
        self.assertIn("YouTube 解析失败", text)
        self.assertIn("boom 42", text)

    def test_unexpected_exception_becomes_parse_error(self):
        text, _ = self._parse_fail(error=RuntimeError("kaboom"))
        self.assertIn("YouTube 解析失败", text)
        self.assertIn("kaboom", text)

    def test_non_dict_result_is_rejected(self):
        text, _ = self._parse_fail(info=None)
        self.assertIn("没有返回视频信息", text)

    def test_missing_yt_dlp_component_is_reported(self):
        """打包缺 yt-dlp 时给出可读提示，而不是裸 ImportError"""
        with patch.dict(sys.modules, {"yt_dlp": None}), \
                patch("app.core.youtube._proxy_url", return_value=""), \
                patch("app.core.youtube._node_path", return_value=""):
            with self.assertRaises(ParseError) as ctx:
                parse_youtube(WATCH_URL)
        self.assertIn("缺少 YouTube 解析组件", str(ctx.exception))


# --------------------------------------------------------------------- #
# sites 分发
# --------------------------------------------------------------------- #
class SitesDispatchTest(unittest.TestCase):
    def _dispatch(self, text):
        sentinel = object()
        with patch("app.core.sites.parse_youtube",
                   return_value=sentinel) as fake:
            got = sites._parse_share(text)
        self.assertIs(sentinel, got)
        fake.assert_called_once()
        return fake.call_args[0][0]

    def test_watch_url_dispatches_to_parse_youtube(self):
        url = self._dispatch(f"看这个 {WATCH_URL} 很有意思")
        self.assertEqual(WATCH_URL, url)

    def test_all_youtube_hosts_dispatch(self):
        cases = (
            "https://m.youtube.com/watch?v=abc",
            "https://music.youtube.com/watch?v=abc",
            "https://youtu.be/abc123",
            "https://www.youtube-nocookie.com/embed/abc123",
            "https://rr1---sn-abc.googlevideo.com/videoplayback?x=1",
        )
        for url in cases:
            with self.subTest(url=url):
                self.assertEqual(url, self._dispatch(url))

    def test_lookalike_url_is_not_dispatched_to_youtube(self):
        """仿冒域不能进 YouTube 分支，应报「暂不支持该链接」"""
        with patch("app.core.sites.parse_youtube") as fake:
            with self.assertRaises(ParseError) as ctx:
                sites._parse_share("https://notyoutube.com/watch?v=abc")
        fake.assert_not_called()
        self.assertIn("暂不支持该链接", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
