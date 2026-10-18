import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image

from app.core import mp4_to_gif as module
from app.core.mp4_to_gif import ConversionCancelled, ConversionError, convert

FFMPEG = Path(__file__).resolve().parents[1] / "dist" / "tools" / "ffmpeg.exe"
FFPROBE = FFMPEG.with_name("ffprobe.exe")


def _run(command, log):
    run = subprocess.run(command, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=log,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    log.seek(0)
    return run, log.read().decode("utf-8", "replace")


def make_two_color_video(path):
    command = [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
               "-f", "lavfi", "-i", "color=c=red:s=96x64:d=0.5:r=10",
               "-f", "lavfi", "-i", "color=c=blue:s=96x64:d=0.5:r=10",
               "-filter_complex",
               "[0:v]format=yuv420p[a];[1:v]format=yuv420p[b];"
               "[a][b]concat=n=2:v=1:a=0,format=yuv420p[out]",
               "-map", "[out]",
               "-c:v", "libx264", "-preset", "ultrafast", str(path)]
    with tempfile.TemporaryFile() as log:
        run, text = _run(command, log)
    if run.returncode:
        raise AssertionError(text)


def gif_frame(path, index=0):
    with Image.open(path) as image:
        image.seek(index)
        return image.convert("RGB").copy()


class Mp4ToGifValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="gif 校验-")
        self.root = Path(self.tmp.name)
        self.source = self.root / "源 视频.mp4"
        self.source.write_bytes(b"not a real video")
        self.out = self.root / "结果.gif"
        self.probe = patch.object(module, "_probe", return_value={})
        self.probe.start()
        self.addCleanup(self.probe.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def convert(self, **kwargs):
        destination = kwargs.pop("destination", self.out)
        options = dict(ffmpeg="fake-ffmpeg")
        options.update(kwargs)
        return convert(self.source, destination, **options)

    def test_output_and_input_paths(self):
        with self.assertRaisesRegex(ConversionError, "找不到输入文件"):
            convert(self.root / "missing.mp4", self.out, ffmpeg="fake")
        with self.assertRaisesRegex(ConversionError, "必须是 .mp4"):
            bad = self.root / "input.mov"
            bad.write_bytes(b"x")
            convert(bad, self.out, ffmpeg="fake")
        empty = self.root / "empty.mp4"
        empty.write_bytes(b"")
        with self.assertRaisesRegex(ConversionError, "输入文件为空"):
            convert(empty, self.out, ffmpeg="fake")
        with self.assertRaisesRegex(ConversionError, "必须为 .gif"):
            convert(self.source, self.root / "out.mp4", ffmpeg="fake")
        with self.assertRaisesRegex(ConversionError, "同一个文件"):
            link = self.root / "别名.gif"
            try:
                os.link(self.source, link)
            except OSError:
                self.skipTest("当前文件系统不支持硬链接")
            convert(self.source, link, ffmpeg="fake", overwrite=True)

    def test_existing_output_requires_overwrite_opt_in(self):
        self.out.write_bytes(b"keep")
        with self.assertRaisesRegex(ConversionError, "输出已存在"):
            self.convert()
        self.assertEqual(self.out.read_bytes(), b"keep")

    def test_parameter_validation(self):
        cases = [
            (dict(fps=0), "帧率"),
            (dict(fps=31), "帧率"),
            (dict(fps="15"), "帧率"),
            (dict(width=8), "宽度"),
            (dict(width=2000), "宽度"),
            (dict(width=True), "宽度"),
            (dict(start=-1), "起始时间"),
            (dict(start=4000), "起始时间"),
            (dict(duration=0), "时长"),
            (dict(duration=301), "时长"),
            (dict(colors=1), "颜色数"),
            (dict(colors=257), "颜色数"),
            (dict(dither="magic"), "抖动"),
            (dict(loop=-2), "循环"),
            (dict(loop=1001), "循环"),
        ]
        for kwargs, message in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(ConversionError, message):
                    self.convert(**kwargs)

    def test_missing_ffmpeg(self):
        with patch.object(module, "ffmpeg_path", return_value=None):
            with self.assertRaisesRegex(ConversionError, "找不到 FFmpeg"):
                convert(self.source, self.out, ffmpeg=None)

    def test_detected_duration_over_limit_is_rejected(self):
        with patch.object(module, "_probe",
                          return_value={"duration": 400.0, "width": 96, "height": 64}):
            with self.assertRaisesRegex(ConversionError, "秒上限"):
                self.convert()
        self.assertFalse(self.out.exists())

    def test_frame_budget_is_checked_before_encoding(self):
        with patch.object(module, "MAX_FRAMES", 5):
            with self.assertRaisesRegex(ConversionError, "帧上限"):
                self.convert(fps=10, duration=1.0)
        self.assertFalse(self.out.exists())

    def test_pre_set_cancel_leaves_no_output(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(ConversionCancelled):
            self.convert(cancel=cancel)
        self.assertFalse(self.out.exists())
        self.assertEqual(list(self.root.glob("mp4-gif-*")), [])


class _FakeProcess:
    def __init__(self, *args, **kwargs):
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


class Mp4ToGifCancelTests(unittest.TestCase):
    def test_cancel_during_encoding_terminates_ffmpeg_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "input.mp4"
            source.write_bytes(b"video")
            destination = root / "out.gif"
            cancel = threading.Event()
            process = _FakeProcess()
            with patch.object(module, "_probe",
                              return_value={"duration": 3.0, "width": 96, "height": 64}), \
                    patch.object(module.subprocess, "Popen", return_value=process):
                timer = threading.Timer(0.3, cancel.set)
                timer.start()
                try:
                    started = time.monotonic()
                    with self.assertRaises(ConversionCancelled):
                        convert(source, destination, fps=10, width=96,
                                ffmpeg="fake-ffmpeg", cancel=cancel)
                    self.assertLess(time.monotonic() - started, 3)
                finally:
                    timer.cancel()
            self.assertTrue(process.terminated)
            self.assertFalse(destination.exists())
            self.assertEqual(list(root.glob("mp4-gif-*")), [])


@unittest.skipUnless(FFMPEG.is_file(), "需要项目 dist/tools/ffmpeg.exe 进行真实编码")
class Mp4ToGifTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not FFPROBE.is_file():
            raise unittest.SkipTest("需要 ffprobe 探测视频时长")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="GIF 转换 测试-")
        self.root = Path(self.tmp.name)
        self.source = self.root / "源 视频 中文.mp4"
        make_two_color_video(self.source)
        self.original = self.source.read_bytes()
        self.out = self.root / "输出 结果.gif"

    def tearDown(self):
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertEqual(list(self.root.glob("mp4-gif-*")), [])
        self.tmp.cleanup()

    def convert(self, **kwargs):
        return convert(self.source, self.out, ffmpeg=FFMPEG, **kwargs)

    def test_converts_dimensions_loop_timing_and_reports_progress(self):
        progress = []
        info = self.convert(fps=10, width=48,
                            progress_cb=lambda *args: progress.append(args))
        self.assertEqual((info["width"], info["height"]), (48, 32))
        self.assertGreaterEqual(info["frames"], 8)
        self.assertLessEqual(info["frames"], 12)
        self.assertGreaterEqual(info["duration_ms"], 800)
        self.assertLessEqual(info["duration_ms"], 1200)
        self.assertEqual(info["loops"], 0)
        with Image.open(self.out) as image:
            self.assertEqual(image.format, "GIF")
            self.assertEqual(image.size, (48, 32))
            self.assertEqual(image.info.get("loop"), 0)
        self.assertTrue(progress)
        self.assertTrue(any(total == 1000 for _, total, _ in progress))
        self.assertTrue(any("正在生成 GIF" in message for _, _, message in progress))
        self.assertTrue(self.out.is_file() and self.out.stat().st_size > 0)

    def test_start_and_duration_choose_requested_segment(self):
        info = self.convert(fps=10, width=96, start=0.7, duration=0.2)
        self.assertGreaterEqual(info["frames"], 1)
        self.assertLessEqual(info["frames"], 4)
        pixel = gif_frame(self.out).getpixel((48, 32))
        self.assertGreater(pixel[2], 150)
        self.assertLess(pixel[0], 120)

    def test_width_none_keeps_source_size_but_respects_limit(self):
        info = self.convert(fps=10, width=None)
        self.assertEqual(info["width"], 96)
        with patch.object(module, "MAX_WIDTH", 64):
            other = self.root / "缩小.gif"
            info = convert(self.source, other, fps=10, width=None, ffmpeg=FFMPEG)
        self.assertEqual(info["width"], 64)

    def test_overwrite_replaces_existing_gif_only_with_opt_in(self):
        self.out.write_bytes(b"keep")
        with self.assertRaisesRegex(ConversionError, "输出已存在"):
            self.convert()
        self.assertEqual(self.out.read_bytes(), b"keep")
        info = self.convert(fps=10, overwrite=True)
        self.assertGreater(info["frames"], 0)
        self.assertTrue(self.out.read_bytes().startswith((b"GIF87a", b"GIF89a")))

    def test_loop_opt_in_and_no_loop(self):
        info = self.convert(fps=5, loop=1)
        self.assertEqual(info["loops"], 1)
        with Image.open(self.out) as image:
            self.assertEqual(image.info.get("loop"), 1)
        other = self.root / "播放一次.gif"
        info = convert(self.source, other, fps=5, loop=-1, ffmpeg=FFMPEG)
        self.assertIsNone(info["loops"])
        with Image.open(other) as image:
            self.assertNotIn("loop", image.info)

    def test_invalid_mp4_reports_ffmpeg_failure(self):
        bad = self.root / "坏文件.mp4"
        bad.write_bytes(b"definitely not a video")
        with self.assertRaisesRegex(ConversionError, "FFmpeg|失败"):
            convert(bad, self.root / "bad.gif", ffmpeg=FFMPEG)


if __name__ == "__main__":
    unittest.main()
