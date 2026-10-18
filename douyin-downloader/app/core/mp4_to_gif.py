# -*- coding: utf-8 -*-
"""纯 MP4 转 GIF 逻辑；由调用方工作线程调用，不依赖 Qt、HTTP 或 CLI。

两遍调色板：先 palettegen 生成全局调色板，再 paletteuse 抖动合成，
避免逐帧独立调色带来的闪烁。音频、字幕、数据流一律丢弃。
临时输出放在目标目录下的临时目录中，成功后才原子发布，取消或失败不触碰
已有文件。输入只读，不修改源视频。
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time

from .ffmpeg import ffmpeg_path

MIN_FPS = 1
MAX_FPS = 30
MIN_WIDTH = 16
MAX_WIDTH = 1920
MIN_COLORS = 2
MAX_COLORS = 256
MAX_START_S = 3600.0
MAX_DURATION_S = 300.0
MAX_FRAMES = 1500
MAX_INPUT_BYTES = 8 * 1024 * 1024 * 1024
DISK_RESERVE = 32 * 1024 * 1024
DITHERS = ("bayer", "none", "floyd_steinberg")


class ConversionError(Exception):
    """输入、参数或编码错误。"""


class ConversionCancelled(ConversionError):
    """用户主动取消；已有输出保持不变。"""


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise ConversionCancelled("转换已取消")


def _report(callback, done, total, message):
    if callback is not None:
        callback(done, total, message)


def _creation_flags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _probe_program(ffmpeg):
    """ffprobe 与 ffmpeg 同目录时优先使用；否则退回 PATH。"""
    folder, _ = os.path.split(ffmpeg or "")
    for name in ("ffprobe.exe", "ffprobe"):
        candidate = os.path.join(folder, name)
        if folder and os.path.isfile(candidate):
            return candidate
    return shutil.which("ffprobe")


def _probe(source, ffmpeg, work):
    """探测时长与分辨率；失败返回空字典，不阻断转换。"""
    probe = _probe_program(ffmpeg)
    out = work / "probe.txt"
    info = {"duration": None, "width": None, "height": None}
    try:
        if probe:
            with out.open("w+b") as log:
                run = subprocess.run(
                    [probe, "-v", "error", "-select_streams", "v:0",
                     "-show_entries", "stream=width,height:format=duration",
                     "-of", "json", str(source)],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    timeout=30, creationflags=_creation_flags())
            text = out.read_text("utf-8", "replace") if out.is_file() else ""
            if run.returncode == 0:
                try:
                    data = json.loads(text)
                    streams = data.get("streams") or [{}]
                    stream = streams[0] if streams else {}
                    info["width"] = int(stream.get("width") or 0) or None
                    info["height"] = int(stream.get("height") or 0) or None
                    info["duration"] = float((data.get("format") or {}).get("duration") or 0) or None
                except (ValueError, TypeError, AttributeError):
                    pass
        if info["duration"] is None:
            # 没有 ffprobe 时用 ffmpeg -i 的日志兜底；无输出时返回码为 1。
            with out.open("w+b") as log:
                subprocess.run(
                    [ffmpeg, "-hide_banner", "-nostdin", "-i", str(source)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=log, timeout=30, creationflags=_creation_flags())
            text = out.read_text("utf-8", "replace") if out.is_file() else ""
            marker = "Duration: "
            if marker in text:
                stamp = text.split(marker, 1)[1].split(",", 1)[0].strip()
                info["duration"] = _parse_clock(stamp)
            if info["width"] is None:
                for line in text.splitlines():
                    if "Video:" in line and "x" in line:
                        # ... Video: h264 ..., 1920x1080 ...
                        for token in line.replace(",", " ").split():
                            if "x" in token:
                                left, _, right = token.partition("x")
                                if left.isdigit() and right.isdigit():
                                    info["width"], info["height"] = int(left), int(right)
                                    break
                        if info["width"]:
                            break
    except (OSError, subprocess.SubprocessError):
        pass
    return info


def _parse_clock(value):
    """解析 ffmpeg 的 HH:MM:SS.ss 或纯秒数，失败返回 None。"""
    try:
        parts = str(value).strip().split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except (ValueError, TypeError, IndexError):
        return None


def _progress_seconds(path):
    """读取 ffmpeg -progress 文件的最后一个 out_time，返回秒数或 None。"""
    try:
        text = path.read_text("utf-8", "replace")
    except OSError:
        return None
    seconds = None
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key == "out_time":
            seconds = _parse_clock(value)
        elif key == "out_time_us" and seconds is None:
            try:
                seconds = int(value) / 1_000_000
            except (ValueError, TypeError):
                pass
    return seconds


def _build_filter(width, fps, colors, dither):
    chain = [f"fps={fps}"]
    if width:
        chain.append(f"scale={width}:-1:flags=lanczos")
    graph = (",".join(chain) +
             ",split[s0][s1];"
             f"[s0]palettegen=max_colors={colors}:stats_mode=diff[p];"
             f"[s1][p]paletteuse=dither={dither}:diff_mode=rectangle")
    return graph


def _validate_options(fps, width, start, duration, colors, dither, loop):
    if isinstance(fps, bool) or not isinstance(fps, (int, float)):
        raise ConversionError("帧率必须为数字")
    if not MIN_FPS <= fps <= MAX_FPS:
        raise ConversionError(f"帧率必须为 {MIN_FPS} 到 {MAX_FPS} 之间")
    if width is not None:
        if isinstance(width, bool) or not isinstance(width, int):
            raise ConversionError("宽度必须为整数或留空")
        if not MIN_WIDTH <= width <= MAX_WIDTH:
            raise ConversionError(f"宽度必须为 {MIN_WIDTH} 到 {MAX_WIDTH} 像素")
    if isinstance(start, bool) or not isinstance(start, (int, float)):
        raise ConversionError("起始时间必须为数字")
    if not 0 <= start <= MAX_START_S:
        raise ConversionError(f"起始时间必须在 0 到 {MAX_START_S:.0f} 秒之间")
    if duration is not None:
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            raise ConversionError("时长必须为数字")
        if not 0 < duration <= MAX_DURATION_S:
            raise ConversionError(f"时长必须大于 0 且不超过 {MAX_DURATION_S:.0f} 秒")
    if isinstance(colors, bool) or not isinstance(colors, int):
        raise ConversionError("颜色数必须为整数")
    if not MIN_COLORS <= colors <= MAX_COLORS:
        raise ConversionError(f"颜色数必须为 {MIN_COLORS} 到 {MAX_COLORS} 之间")
    if dither not in DITHERS:
        raise ConversionError("抖动方式不受支持")
    if isinstance(loop, bool) or not isinstance(loop, int):
        raise ConversionError("循环次数必须为整数")
    if not -1 <= loop <= 1000:
        raise ConversionError("循环次数必须为 -1 到 1000 之间（-1 不循环，0 无限循环）")


def _encode(command, log_path, progress_path, cancel, progress_cb, total_ms):
    # 不使用 PIPE：FFmpeg 的 stderr 与进度写入文件，管道不会因缓冲填满而阻塞。
    with log_path.open("w+b") as log:
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=log, creationflags=_creation_flags(),
        )
        try:
            next_report = 0.0
            while process.poll() is None:
                _check_cancel(cancel)
                now = time.monotonic()
                if progress_cb is not None and now >= next_report:
                    seconds = _progress_seconds(progress_path) or 0.0
                    done = int(seconds * 1000)
                    if total_ms:
                        done = max(0, min(done, total_ms))
                        message = f"正在生成 GIF … {seconds:.1f} / {total_ms / 1000:.1f} 秒"
                    else:
                        message = f"正在生成 GIF … 已处理 {seconds:.1f} 秒"
                    _report(progress_cb, done, total_ms, message)
                    next_report = now + 0.2
                if cancel is not None:
                    cancel.wait(0.05)
                else:
                    time.sleep(0.05)
            _check_cancel(cancel)
            if process.returncode:
                log.seek(0, os.SEEK_END)
                log.seek(max(0, log.tell() - 8192))
                detail = log.read().decode("utf-8", errors="replace").strip()
                raise ConversionError(f"FFmpeg 生成 GIF 失败（{process.returncode}）：{detail}")
        finally:
            # 回调异常同样不能遗留子进程。
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def _gif_info(path):
    """用 Pillow 读取 GIF 实际帧数、尺寸、单帧毫秒与循环标志。"""
    from PIL import Image

    try:
        with Image.open(path) as image:
            if image.format != "GIF":
                raise ConversionError("FFmpeg 输出不是有效 GIF")
            frames = int(getattr(image, "n_frames", 1) or 1)
            width, height = image.size
            duration = 0
            for index in range(frames):
                image.seek(index)
                duration += int(image.info.get("duration", 0) or 0)
            loop = image.info.get("loop")
            loop = int(loop) if loop is not None else None
        return dict(frames=frames, duration_ms=duration, width=width,
                    height=height, loops=loop)
    except ConversionError:
        raise
    except Exception as exc:
        raise ConversionError(f"无法读取生成的 GIF：{exc}") from exc


def convert(source, destination, *, fps=15, width=480, start=0.0, duration=None,
            colors=256, dither="bayer", loop=0, overwrite=False,
            cancel: threading.Event | None = None, progress_cb=None,
            ffmpeg=None) -> dict:
    """把单个 MP4 转为循环 GIF。

    width=None 表示不缩放（超过 1920 像素时仍会缩到 1920）；duration=None
    表示从 start 到结尾，最长 300 秒、最多 1500 帧（GIF 体积与解码成本上限）。
    loop=-1 不循环、0 无限循环、正整数为重复次数。
    progress_cb(done_ms, total_ms, message) 中 total_ms 在时长未知时为 0。
    默认通过排他硬链接原子发布，overwrite=True 时用 os.replace 原子替换；
    失败或取消不会留下输出残片。
    """
    try:
        return _convert(source, destination, fps, width, start, duration, colors,
                        dither, loop, overwrite, cancel, progress_cb, ffmpeg)
    except ConversionError:
        raise
    except Exception as exc:
        raise ConversionError(f"MP4 转 GIF 失败：{exc}") from exc


def _convert(source, destination, fps, width, start, duration, colors, dither,
             loop, overwrite, cancel, progress_cb, ffmpeg):
    _check_cancel(cancel)
    source = Path(source)
    destination = Path(destination).absolute()
    if not source.is_file():
        raise ConversionError(f"找不到输入文件：{source}")
    if source.suffix.lower() != ".mp4":
        raise ConversionError("输入文件必须是 .mp4")
    if source.stat().st_size == 0:
        raise ConversionError("输入文件为空")
    if source.stat().st_size > MAX_INPUT_BYTES:
        raise ConversionError("输入文件超过 8 GiB 限制")
    if destination.suffix.lower() != ".gif":
        raise ConversionError("输出文件扩展名必须为 .gif")
    if (source.resolve() == destination.resolve() or
            (destination.exists() and os.path.samefile(source, destination))):
        raise ConversionError("输入和输出不能是同一个文件")
    if os.path.lexists(destination) and not overwrite:
        raise ConversionError(f"输出已存在：{destination}")
    _validate_options(fps, width, start, duration, colors, dither, loop)

    executable = ffmpeg if ffmpeg is not None else ffmpeg_path()
    if not executable:
        raise ConversionError("找不到 FFmpeg，请先安装或下载 FFmpeg 组件")

    destination.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(destination.parent).free < DISK_RESERVE:
        raise ConversionError("输出磁盘剩余空间不足 32 MiB")
    _report(progress_cb, 0, 0, "正在读取视频信息 …")

    with tempfile.TemporaryDirectory(prefix="mp4-gif-", dir=destination.parent) as folder:
        work = Path(folder)
        meta = _probe(source, executable, work)
        total_duration = meta.get("duration")
        if start and total_duration and start >= total_duration:
            raise ConversionError(
                f"起始时间 {start:g} 秒超出视频时长 {total_duration:.1f} 秒")
        clip = duration
        if clip is None and total_duration:
            clip = max(0.0, total_duration - start)
        if clip is not None and clip > MAX_DURATION_S:
            raise ConversionError(
                f"转换时长 {clip:.1f} 秒超过 {MAX_DURATION_S:.0f} 秒上限；"
                "请填写更短的转换时长")

        # 输出宽度：显式宽度优先；不缩放时仍然不超过 1920 像素。
        out_width = width
        if out_width is None:
            source_width = meta.get("width")
            out_width = min(source_width, MAX_WIDTH) if source_width else None

        if clip:
            predicted = math.ceil(clip * fps)
            if predicted > MAX_FRAMES:
                raise ConversionError(
                    f"按当前帧率会生成约 {predicted} 帧，超过 {MAX_FRAMES} 帧上限；"
                    "请降低帧率、缩小宽度或缩短时长")

        total_ms = int(clip * 1000) if clip else 0
        _check_cancel(cancel)
        progress_path = work / "progress.txt"
        encoded = work / "encoded.gif"
        command = [str(executable), "-hide_banner", "-loglevel", "error",
                   "-nostdin", "-y", "-progress", str(progress_path),
                   "-nostats", "-stats_period", "0.25"]
        if start:
            command += ["-ss", f"{float(start):.3f}"]
        if clip:
            command += ["-t", f"{float(clip):.3f}"]
        command += ["-i", str(source), "-an", "-sn", "-dn",
                    "-vf", _build_filter(out_width, fps, colors, dither),
                    "-loop", str(loop), "-f", "gif", str(encoded)]
        _encode(command, work / "ffmpeg.log", progress_path, cancel,
                progress_cb, total_ms)
        _check_cancel(cancel)
        if not encoded.is_file() or encoded.stat().st_size == 0:
            raise ConversionError("FFmpeg 未生成有效输出")
        info = _gif_info(encoded)
        if info["duration_ms"] > MAX_DURATION_S * 1000 + 1000:
            raise ConversionError(
                f"生成的 GIF 时长 {info['duration_ms'] / 1000:.1f} 秒超过 "
                f"{MAX_DURATION_S:.0f} 秒上限；请缩短转换时长")
        if info["frames"] > MAX_FRAMES:
            raise ConversionError(
                f"生成的 GIF 共 {info['frames']} 帧，超过 {MAX_FRAMES} 帧上限；"
                "请降低帧率、缩小宽度或缩短时长")
        _report(progress_cb, total_ms, total_ms, "编码完成，正在保存 …")
        _check_cancel(cancel)
        if overwrite:
            os.replace(encoded, destination)
        else:
            # 原子排他创建：即使编码期间另一任务创建目标，也绝不覆盖。
            try:
                os.link(encoded, destination)
            except FileExistsError as exc:
                raise ConversionError(f"输出已存在：{destination}") from exc
            except OSError as exc:
                raise ConversionError(
                    f"无法原子发布输出（需要支持硬链接的本地文件系统）：{exc}") from exc
    return info
