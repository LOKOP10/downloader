# -*- coding: utf-8 -*-
"""导出原始帧的纯逻辑测试：时间解析、命令拼装、帧文件排序、探测值解析。

不需要 ffmpeg —— 真实导出由 tests/test_frame_export_ffmpeg.py 里的集成用例覆盖
（找不到 ffmpeg 时自动 skip）。
"""
import os
import tempfile
from pathlib import Path
import unittest

from app.core.frame_export import (FrameExportError, build_command, format_time,
                                   frame_files, parse_time, _parse_rate)


class TimeParseTest(unittest.TestCase):
    def test_plain_seconds(self):
        self.assertAlmostEqual(parse_time("83.5"), 83.5)
        self.assertAlmostEqual(parse_time("  12 "), 12.0)

    def test_two_parts_are_minutes_and_seconds(self):
        # 曾经踩过：正则可选组把「01:23.5」贪婪成 1 小时 23.5 秒
        self.assertAlmostEqual(parse_time("01:23.5"), 83.5)

    def test_three_parts(self):
        self.assertAlmostEqual(parse_time("00:01:23.500"), 83.5)
        self.assertAlmostEqual(parse_time("1:00:00"), 3600.0)

    def test_roundtrip_with_format_time(self):
        for seconds in (0.0, 1.5, 83.5, 3599.999, 3661.25):
            self.assertAlmostEqual(parse_time(format_time(seconds)), seconds, places=3)

    def test_invalid(self):
        for bad in ("", "  ", "abc", "1:2:3:4", "-5", "1:xx"):
            with self.assertRaises(ValueError):
                parse_time(bad)


class FormatTimeTest(unittest.TestCase):
    def test_shape(self):
        self.assertEqual(format_time(83.5), "00:01:23.500")
        self.assertEqual(format_time(0), "00:00:00.000")
        self.assertEqual(format_time(3661.25), "01:01:01.250")

    def test_negative_clamped(self):
        self.assertEqual(format_time(-3), "00:00:00.000")


class CommandTest(unittest.TestCase):
    def test_seek_arguments_after_input(self):
        cmd = build_command("in.mp4", "out", 1.5, 2.0, "png", 1,
                            progress_path="pf.txt", ffmpeg="ffmpeg")
        self.assertEqual(cmd[0], "ffmpeg")
        self.assertLess(cmd.index("-i"), cmd.index("-ss"),
                        "-ss 必须在 -i 之后才是逐帧精确定位")
        self.assertLess(cmd.index("-ss"), cmd.index("-t"))
        self.assertEqual(cmd[cmd.index("-t") + 1], "2.000")
        self.assertEqual(cmd[cmd.index("-fps_mode") + 1], "passthrough")
        # 三种格式统一 8bit RGB，否则 tiff 会被存成 yuv420p（色度 4:2:0 抽样）
        self.assertEqual(cmd[cmd.index("-pix_fmt") + 1], "rgb24")
        self.assertEqual(cmd[cmd.index("-sws_flags") + 1], "accurate_rnd+full_chroma_int")
        self.assertEqual(cmd[-1], os.path.join("out", "%06d.png"))
        self.assertEqual(cmd[cmd.index("-progress") + 1], "pf.txt")

    def test_compression_level_only_for_png_tiff(self):
        png = build_command("a.mp4", "o", 0, 1, "png", 3)
        self.assertEqual(png[png.index("-compression_level") + 1], "3")
        bmp = build_command("a.mp4", "o", 0, 1, "bmp", 3)
        self.assertNotIn("-compression_level", bmp)
        self.assertEqual(bmp[-1], os.path.join("o", "%06d.bmp"))

    def test_unknown_format(self):
        with self.assertRaises(FrameExportError):
            build_command("a.mp4", "o", 0, 1, "jpg", 1)


class RateTest(unittest.TestCase):
    def test_fraction_and_plain(self):
        self.assertAlmostEqual(_parse_rate("30000/1001"), 29.970, places=3)
        self.assertAlmostEqual(_parse_rate("60"), 60.0)
        self.assertEqual(_parse_rate("0/0"), 0.0)
        self.assertEqual(_parse_rate("N/A"), 0.0)
        self.assertEqual(_parse_rate(None), 0.0)


class FrameFilesTest(unittest.TestCase):
    def test_natural_order(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ("000001.png", "000010.png", "000002.png"):
                Path(folder, name).write_bytes(b"x")
            Path(folder, "note.txt").write_text("x")
            names = [os.path.basename(p) for p in frame_files(folder, "png")]
            self.assertEqual(names, ["000001.png", "000002.png", "000010.png"])
            self.assertEqual(frame_files(os.path.join(folder, "missing"), "png"), [])


if __name__ == "__main__":
    unittest.main()
