# -*- coding: utf-8 -*-
"""导出视频原始帧：把指定时间段的每一帧原样导出成图片。

口径沿用外置 bat 脚本（`-fps_mode passthrough` 不抽帧不补帧、
`sws_flags accurate_rnd+full_chroma_int` 精确保留色彩、`compression_level 1`
比默认快约三成），搬进 GUI 后补三件事：

1. **时间段两端夹住**：`-ss` 起点 + `-t` 时长，都放在 `-i` 之后（逐帧精确定位，
   不会因为关键帧对齐而漂）；
2. **进度 + 取消**：靠 `-progress` 文件的 `frame=` 字段报已导出帧数；
3. **可回放**：输出按 `%06d` 顺序命名，交给 `view/frame_player.py` 换帧播放。

本模块不依赖 Qt，可单独跑：
    python -m app.core.frame_export <视频> <起点秒> <终点秒>
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from typing import Callable, Optional

# 与 bat 一致：png 无损（推荐）/ bmp 完全不压缩 / tif
FORMATS = ("png", "bmp", "tif")

# 只对 png/tif 有效；bmp 不认这个参数，传了会打 warning
_COMPRESSIBLE = ("png", "tif")


class FrameExportError(RuntimeError):
    """导出失败（ffmpeg 报错、参数非法等）"""


class FrameExportCancelled(FrameExportError):
    """用户取消"""


# ---------------------------------------------------------------- 时间
def parse_time(text: str) -> float:
    """把 `83.5` / `01:23.5` / `00:01:23.500` 解析成秒。

    按冒号分段数决定量纲：1 段=秒、2 段=分:秒、3 段=时:分:秒。
    不用正则里的可选组——那会把 `01:23.5` 贪婪成「1 小时 23.5 秒」。
    """
    raw = (text or "").strip()
    if not raw:
        raise ValueError("时间不能为空")
    parts = raw.split(":")
    if len(parts) > 3:
        raise ValueError(f"看不懂的时间：{raw}（示例 00:01:23.500 或 83.5）")
    try:
        values = [float(part) for part in parts]
    except ValueError as error:
        raise ValueError(f"看不懂的时间：{raw}（示例 00:01:23.500 或 83.5）") from error
    if any(value < 0 for value in values):
        raise ValueError("时间不能是负数")
    total = 0.0
    for value in values:
        total = total * 60 + value
    return total


def format_time(seconds: float) -> str:
    """秒 → `00:01:23.500`（毫秒精度，便于直接回填输入框）"""
    total_ms = int(round(max(0.0, float(seconds)) * 1000))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    return f"{total_s // 3600:02d}:{total_s % 3600 // 60:02d}:{total_s % 60:02d}.{ms:03d}"


# ---------------------------------------------------------------- 探测
def ffprobe_path(ffmpeg: str = "") -> Optional[str]:
    """与 ffmpeg 同目录的 ffprobe；找不到再退回 PATH"""
    if ffmpeg:
        cand = os.path.join(os.path.dirname(ffmpeg), "ffprobe.exe")
        if os.path.exists(cand):
            return cand
    return shutil.which("ffprobe")


def _parse_rate(value) -> float:
    """'30000/1001' → 29.97；坏值返回 0"""
    try:
        text = str(value or "").strip()
        if "/" in text:
            num, den = text.split("/", 1)
            den_f = float(den)
            return float(num) / den_f if den_f else 0.0
        return float(text)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def probe_video(source: str, ffmpeg: str = "") -> dict:
    """读时长 / 帧率 / 分辨率 / 容器里记录的总帧数（可能为 0，交给调用方兜底）"""
    probe = ffprobe_path(ffmpeg)
    if not probe:
        raise FrameExportError("找不到 ffprobe，无法读取视频信息")
    if not os.path.exists(source):
        raise FrameExportError(f"文件不存在：{source}")
    command = [
        probe, "-v", "error", "-select_streams", "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,avg_frame_rate,nb_frames,duration",
        "-show_entries", "format=duration",
        "-of", "json", source,
    ]
    try:
        done = subprocess.run(command, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=60, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        raise FrameExportError(f"读取视频信息失败：{error}") from error
    if done.returncode != 0:
        detail = done.stderr.decode("utf-8", "replace").strip().splitlines()
        raise FrameExportError("读取视频信息失败：" + (detail[-1] if detail else "未知错误"))
    try:
        payload = json.loads(done.stdout.decode("utf-8", "replace") or "{}")
    except ValueError as error:
        raise FrameExportError(f"读取视频信息失败：{error}") from error
    streams = payload.get("streams") or [{}]
    stream = streams[0] or {}
    fps = _parse_rate(stream.get("avg_frame_rate")) or _parse_rate(stream.get("r_frame_rate"))
    duration = 0.0
    for raw in (stream.get("duration"), (payload.get("format") or {}).get("duration")):
        try:
            duration = float(raw)
        except (TypeError, ValueError):
            continue
        if duration > 0:
            break
    frames = 0
    try:
        frames = int(stream.get("nb_frames") or 0)
    except (TypeError, ValueError):
        frames = 0
    if duration <= 0 and frames > 0 and fps > 0:
        duration = frames / fps
    return {
        "duration": duration,
        "fps": fps,
        "frames": frames,
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
    }


# ---------------------------------------------------------------- 导出
def frame_files(out_dir: str, ext: str = "png") -> list:
    """按序号自然排序返回已导出的帧文件"""
    suffix = "." + ext.lower().lstrip(".")
    try:
        names = [n for n in os.listdir(out_dir) if n.lower().endswith(suffix)]
    except OSError:
        return []
    names.sort(key=lambda n: int(re.sub(r"\D", "", os.path.splitext(n)[0]) or 0))
    return [os.path.join(out_dir, n) for n in names]


def _progress_frame(progress_path: str) -> int:
    """读 `-progress` 文件里最后一个 frame= 值；读不到返回 0"""
    try:
        with open(progress_path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return 0
    value = 0
    for line in text.splitlines():
        if line.startswith("frame="):
            try:
                value = int(line.split("=", 1)[1])
            except (IndexError, ValueError):
                continue
    return value


def build_command(source: str, out_dir: str, start: float, duration: float,
                  fmt: str = "png", level: int = 1,
                  progress_path: str = "", ffmpeg: str = "ffmpeg") -> list:
    """拼 ffmpeg 命令（单独抽出来便于测试断言参数顺序）"""
    suffix = fmt.lower().lstrip(".")
    if suffix not in FORMATS:
        raise FrameExportError(f"不支持的输出格式：{fmt}")
    command = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-nostats"]
    if progress_path:
        command += ["-progress", progress_path]
    command += ["-y", "-i", source]
    # -ss / -t 放在 -i 之后：解码级精确定位，逐帧不漂
    command += ["-ss", f"{start:.3f}", "-t", f"{duration:.3f}"]
    command += ["-map", "0:v:0", "-fps_mode", "passthrough",
                "-sws_flags", "accurate_rnd+full_chroma_int",
                # 统一输出 8bit RGB：不显式指定时 ffmpeg 的 tiff 编码器会保留
                # yuv420p，色度被 4:2:0 抽样 —— 实测同帧 TIFF 与 PNG/BMP 的原始
                # 像素 md5 不同（d2e4b9ce… vs f892cca8…）；加这句后三者逐字节一致。
                "-pix_fmt", "rgb24"]
    if suffix in _COMPRESSIBLE:
        command += ["-compression_level", str(max(0, min(9, int(level))))]
    command += ["-start_number", "1", os.path.join(out_dir, f"%06d.{suffix}")]
    return command


def export_frames(source: str, out_dir: str, start: float, end: float,
                  fmt: str = "png", level: int = 1,
                  cancel=None, progress_cb: Optional[Callable[[int, int, str], None]] = None,
                  ffmpeg: str = "") -> dict:
    """导出 [start, end) 区间的全部原始帧，返回 {frames, out_dir, elapsed, expected}"""
    if not ffmpeg:
        raise FrameExportError("未找到 FFmpeg")
    duration = float(end) - float(start)
    if duration <= 0:
        raise FrameExportError("结束时间必须晚于开始时间")
    info = probe_video(source, ffmpeg)
    fps = info.get("fps") or 0.0
    if info.get("duration") and start >= info["duration"] - 1e-3:
        raise FrameExportError(
            f"开始时间超出视频时长（{format_time(info['duration'])}）")
    expected = int(round(duration * fps)) if fps > 0 else 0
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as error:
        raise FrameExportError(
            f"输出目录建不了：{out_dir}（{error.strerror or error}）\n"
            "把视频放到有写权限的目录再试，或先手动建好这个文件夹。") from error
    progress_path = os.path.join(out_dir, ".frame_export_progress")
    command = build_command(source, out_dir, start, duration, fmt, level,
                            progress_path=progress_path, ffmpeg=ffmpeg)
    started = time.time()
    log_path = os.path.join(out_dir, ".frame_export_err.log")
    try:
        with open(log_path, "wb") as log:
            process = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if progress_cb:
                progress_cb(0, expected or 0, f"正在导出：0{f'/{expected}' if expected else ''} 帧")
            last_report = 0.0
            while process.poll() is None:
                if cancel is not None and cancel.is_set():
                    process.kill()
                    process.wait(timeout=10)
                    raise FrameExportCancelled("已取消")
                time.sleep(0.15)
                now = time.time()
                if progress_cb and now - last_report >= 0.2:
                    last_report = now
                    done_frames = _progress_frame(progress_path)
                    message = f"正在导出：{done_frames} 帧"
                    if expected:
                        message += f"/{expected}"
                    progress_cb(done_frames, expected or 0, message)
            if process.returncode != 0:
                raise FrameExportError(_read_error(log_path))
    finally:
        for temporary in (progress_path, log_path):
            try:
                os.remove(temporary)
            except OSError:
                pass
    files = frame_files(out_dir, fmt)
    if not files:
        raise FrameExportError("ffmpeg 没有产出任何帧：请检查时间段是否落在画面内")
    if progress_cb:
        progress_cb(len(files), len(files), f"已导出 {len(files)} 帧")
    return {"frames": len(files), "out_dir": out_dir,
            "elapsed": time.time() - started, "expected": expected}


def _read_error(log_path: str) -> str:
    """把 ffmpeg 的 stderr 压成一句人话（最后一行通常是真正的错因）"""
    try:
        with open(log_path, "rb") as handle:
            text = handle.read().decode("utf-8", "replace").strip()
    except OSError:
        return "ffmpeg 执行失败（没有错误日志）"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return "ffmpeg 执行失败（没有错误输出）"
    return "导出失败：" + lines[-1]


def _main(argv=None) -> int:
    """命令行冒烟：python -m app.core.frame_export <视频> <起> <止> [格式]"""
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 3:
        print("用法：python -m app.core.frame_export <视频> <起点> <终点> [png|bmp|tif]")
        return 2
    source, start_text, end_text = args[0], args[1], args[2]
    fmt = args[3] if len(args) > 3 else "png"
    from app.core.ffmpeg import ffmpeg_path
    ffmpeg = ffmpeg_path() or "ffmpeg"
    start, end = parse_time(start_text), parse_time(end_text)
    out_dir = os.path.splitext(source)[0] + "_frames"
    def _report(done, total, message):
        print(f"\r{message}", end="", flush=True)
    info = export_frames(source, out_dir, start, end, fmt, 1, None, _report, ffmpeg)
    print()
    print(f"完成：{info['frames']} 帧，用时 {info['elapsed']:.1f}s → {info['out_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
