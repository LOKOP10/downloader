# -*- coding: utf-8 -*-
"""纯 Python 传输引擎与路径/签名/完整性校验原语（不依赖 Qt）。"""
import os
import re
import threading
import time
from collections import deque

import requests

from .applog import get_logger

log = get_logger("download")


CHUNK = 256 * 1024

# --------------------------------------------------------------------------- #
# 多连接分段下载（IDM 式）
#
# 实测（B站 CDN，64MB）：单连接 13.0 MB/s，4 连接 23.8 MB/s（1.83x），
# 8 连接 23.6 MB/s（不再提升，已到带宽上限）。瓶颈是单连接的拥塞窗口，
# 不是缓冲区大小（把 CHUNK 从 256K 调到 1M 反而略慢）。
#
# 因此这里不写死「甜点值」，而是照 IDM 的做法把连接数交给用户：默认 8 条，
# 可调 1-32，带宽不够时多开的连接只是排队，不会更慢。
# --------------------------------------------------------------------------- #
SEGMENT_ENABLED = True           # 总开关（设置页可关）
SEGMENT_MIN_SIZE = 2 * 1024 * 1024   # 小于 2MB 不分片：连接开销大于收益
SEGMENT_MAX = 32                 # 每文件并发连接数上限（与 IDM 一致：1-32）
SEGMENT_DEFAULT_CONNECTIONS = 8  # 默认每文件连接数（IDM 默认也是 8）
CONN_BUDGET = 64                 # 全局连接预算：并发任务多时按此摊薄
CONNECT_TIMEOUT = 6              # 建连超时：坏掉的 CDN 要尽快判死好换下一个候选
READ_TIMEOUT = 20                # 读超时：这么久一个字节都没来就判这条连接废了
HTTP_TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)
PROBE_TIMEOUT = (min(CONNECT_TIMEOUT, 5), 8)   # 元数据探测超时：节点连不上或死等时不拖延切换备用源
FFMPEG_TIMEOUT = 900             # ffmpeg 合并上限（秒），超时判失败，别永远挂在「合并中」
SEGMENT_CHUNK_MIN = 1024 * 1024        # 动态分块的块大小下限
SEGMENT_CHUNK_MAX = 4 * 1024 * 1024    # 上限：太大慢连接会拖尾，太小请求太碎
CHUNK_SLOW_AFTER = 8.0                 # 一个分块跑满这么久才判定"这条连接太慢"
CHUNK_SLOW_RATIO = 0.30                # 慢于全场最快连接的这个比例就考虑重排
CHUNK_MIN_REMAINING_TIME = 4.0         # 按当前速度很快能下完时，省去重连开销
CHUNK_MAX_ATTEMPTS = 4                 # 同一分块最多重排几次（防死循环）
ABANDON_MIN_BUDGET = 8                 # 放弃重排的总次数预算下限


# 一个下载任务会用到的全部落点后缀（空串表示最终文件自身）。
# 与 DownloadManager.TEMP_SUFFIXES 语义一致，但必须模块级可用，
# 因为 prepare_target_path 在类定义之前就要用到它。
TEMP_PATH_SUFFIXES = ("", ".part", ".vpart", ".apart", ".mpart",
                      ".part.meta", ".vpart.meta", ".apart.meta",
                      ".mpart.meta")


def prepare_target_path(save_path: str) -> str:
    """把目标路径归一化，并确保它的父目录存在、可写。

    返回归一化后的绝对路径。

    为什么必须单独做这件事：

      1. **父目录不存在**时直接 open() 抛 FileNotFoundError，错误信息里只有
         一个路径，用户看不出是「目录没建出来」；
      2. **目标位置已经有个同名目录**时，Windows 上 open(path, "wb") 抛的是
         PermissionError: [Errno 13] Permission denied —— 与真正的「没权限」
         完全同一个错误码。实测（在可写目录里预先建一个 x.mp4.part 目录）复现
         的就是这条报错。原实现把这个异常原样抛给用户，于是「位置被目录占用」
         被误读成「目录没权限」，改权限、换目录都不解决问题；
      3. 混合分隔符（F:/a\\b）在 Windows/Python 下本身可用，不是根因；
         但归一化后日志、去重、路径比较都更可靠。

    因此这里分情况给出**可操作**的错误，而不是让底层 OSError 裸奔。
    """
    path = os.path.abspath(os.path.normpath(save_path))
    parent = os.path.dirname(path)

    # 父目录：不存在就建（用户选盘符根目录时 parent 就是 F:\\，已存在，跳过）
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as e:
            raise PermissionError(
                "无法创建下载目录：" + parent + "\\n"
                "原因：" + (e.strerror or str(e)) + "\\n"
                "请换一个可写的目录，或在系统里给该路径授予写权限。") from e

    # 目标位置被同名目录占用：这是 Errno 13 最常见的真实成因，必须单独说清楚
    # 目标位置被同名目录占用：这是 Errno 13 最常见的真实成因，必须单独说清楚。
    # 临时文件位置也要一起查：用户报错那次最终文件还不存在，是 .part 那个
    # 位置躺着一个同名目录 —— 只查 save_path 会漏掉它，要等 open(.part)
    # 才抛出含义模糊的 Errno 13。
    for suffix in TEMP_PATH_SUFFIXES:
        probe = path + suffix
        if os.path.isdir(probe):
            raise IsADirectoryError(
                "下载路径被同名文件夹占用：" + probe + "\n"
                "请删除或重命名该文件夹后重试（它不是本程序创建的下载文件）。")

    # 真正不可写时给明确指引；这里只做探测，不写入任何内容
    if not os.access(parent, os.W_OK):
        raise PermissionError(
            "下载目录不可写：" + parent + "\\n"
            "请换一个目录，或在系统里给该路径授予写权限。")

    return path


def ensure_temp_writable(tmp_path: str) -> None:
    """分段下载前确认临时文件**能真正被创建**，失败时给出准确原因。

    单独抽出来是为了把「创建失败」与「写入中途失败」区分开：
    前者是路径/权限问题（换地址重试毫无意义），后者才值得降级单连接。
    """
    # Windows 上 open(目录, "ab") 抛的是 PermissionError（不是 IsADirectoryError），
    # 所以必须先显式判断「这个位置是不是目录」，否则会误报成权限问题。
    if os.path.isdir(tmp_path):
        raise IsADirectoryError(
            "临时文件位置被同名文件夹占用：" + tmp_path + "\\n"
            "请删除或重命名该文件夹后重试。") from None
    try:
        with open(tmp_path, "ab"):
            pass
    except IsADirectoryError as e:
        raise IsADirectoryError(
            "临时文件位置被同名文件夹占用：" + tmp_path + "\\n"
            "请删除或重命名该文件夹后重试。") from e
    except PermissionError as e:
        raise PermissionError(
            "没有权限写入临时文件：" + tmp_path + "\\n"
            "原因：" + (e.strerror or str(e)) + "\\n"
            "请检查该目录的写权限，或换一个下载目录。") from e
    except OSError as e:
        raise OSError(
            "无法创建临时文件：" + tmp_path + "\\n"
            "原因：" + (e.strerror or str(e))) from e


# 抖音 CDN 直链是**时效签名**：URL 里的 l=YYYYMMDDHHMMSS... 是签发时刻，
# 过期后服务端对所有镜像统一回 403 Forbidden。
# 实测：17:29 解析的链接，21:17 重试时 15 个候选地址全部 403 ——
# 用户只看到「403」，不知道是链接过期，只能反复重试。
#
# 这里按签发时刻判断，过期就给出明确指引，不再白跑 15 个地址。
SIGNED_URL_TTL_SECONDS = 30 * 60      # 抖音签名保守按 30 分钟处理


def signed_url_issued_at(url: str) -> float:
    """从抖音直链里取签发时间戳（l=20261016172945FA19...），失败返回 0。

    只解析 l= 参数：它是 14 位 YYYYMMDDHHMMSS，后面紧跟一串校验字符。
    """
    import datetime
    m = re.search(r"[?&]l=(\d{14})", url or "")
    if not m:
        return 0.0
    try:
        dt = datetime.datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
        return dt.timestamp()
    except ValueError:
        return 0.0


def signed_url_expired(url: str, ttl: int = SIGNED_URL_TTL_SECONDS) -> bool:
    """链接是否已过期。取不到签发时间时返回 False（不误伤非抖音地址）。"""
    issued = signed_url_issued_at(url)
    if issued <= 0:
        return False
    return (time.time() - issued) > ttl


def content_range_total(value: str) -> int:
    """从 Content-Range（`bytes 100-999/1000`）里解析资源总长度，失败返回 0"""
    if not value or "/" not in value:
        return 0
    tail = value.rsplit("/", 1)[-1].strip()
    if not tail.isdigit():
        return 0      # 形如 `bytes 0-99/*`：服务端不知道总长
    return int(tail)


def _parse_content_range(value: str) -> tuple:
    """解析 Content-Range，返回 (start, end, total, 是否可信)。"""
    try:
        unit, rest = value.split(" ", 1)
        range_part, total_part = rest.split("/", 1)
        start_text, end_text = range_part.split("-", 1)
        start, end = int(start_text), int(end_text)
        total = int(total_part) if total_part.strip().isdigit() else 0
    except (ValueError, IndexError, AttributeError):
        return 0, 0, 0, False
    return start, end, total, unit.lower() == "bytes" and end >= start


def _resource_id(headers) -> str:
    """取能唯一标识"这份资源"的响应头（用于判断续传时服务端是否换了源）

    字节总长度相同的两份不同视频无法靠长度区分，因此优先用 ETag /
    Last-Modified / Content-Disposition 这类强标识；都拿不到时返回空串
    （此时只做长度校验，即退化到旧行为）。
    """
    for key in ("ETag", "Last-Modified", "Content-Disposition"):
        val = (headers.get(key) or "").strip()
        if val:
            return f"{key}={val}"
    return ""


def _body_definitely_complete(resp) -> bool:
    """响应体是否「可以确定已经完整收完」

    只在调用方拿不到总长度（无 Content-Length、探测也不明）时用于兜底判定：
    - 有 Content-Length：长度已知，完整性交给上面的字节比对；
    - Transfer-Encoding 含 chunked：requests 只有在读到终止块时才会正常结束，
      提前断开会抛 ChunkedEncodingError（不会走到这里），故视为完整；
    - 两者都没有：靠连接关闭界定 body，提前断开与正常结束无法区分 —— 不可信。
    """
    headers = getattr(resp, "headers", None) or {}
    if (headers.get("Content-Length") or "").strip():
        return True
    encoding = (headers.get("Transfer-Encoding") or "").lower()
    return "chunked" in encoding


def _should_reconnect_slow_chunk(got: int, length: int, elapsed: float,
                                 best: float, budget_available: bool) -> bool:
    """只重排明显落后且继续下载还需较久的分块。"""
    if (not budget_available or got <= 0 or got >= length
            or elapsed < CHUNK_SLOW_AFTER or best <= 0):
        return False
    rate = got / elapsed
    return (rate < best * CHUNK_SLOW_RATIO
            and (length - got) / rate > CHUNK_MIN_REMAINING_TIME)


class SignedUrlExpired(Exception):
    """下载直链的时效签名已过期：换地址/重试都无用，必须重新解析。

    单独一类是为了让调用方**不要**把它当成可重试的网络错误 ——
    否则会白白跑完所有候选地址（实测 15 个）再失败。
    """
    pass


class _Cancelled(Exception):
    pass


class _SlowChunk(Exception):
    """当前分块跑得太慢：放弃剩余部分，换一条连接重排（见 _stream_segments）"""
    pass


class IncompleteDownload(Exception):
    """下载字节数少于 Content-Length，判定为截断，需续传重试"""
    pass


class MissingDependency(Exception):
    """缺少运行依赖（如 ffmpeg），重试与换地址都无法解决"""
    pass


def probe_total(url: str, headers: dict, *, http_get=None) -> tuple:
    """探测资源总长度与是否支持分段，返回 (total, 是否支持 Range)"""
    getter = http_get or requests.get
    h = dict(headers)
    h["Range"] = "bytes=0-0"
    try:
        with getter(url, headers=h, stream=True, timeout=PROBE_TIMEOUT) as resp:
            if resp.status_code == 206:
                total = content_range_total(resp.headers.get("Content-Range", ""))
                if total:
                    return total, True
            elif resp.status_code == 200:
                return int(resp.headers.get("Content-Length", 0) or 0), False
    except (requests.Timeout, requests.exceptions.SSLError):
        # 节点连接/首包超时或 TLS 握手被重置：直接交由上层切换备用 CDN，
        # 避免返回 (0, False) 后再用同一坏地址进单连接白白重等一轮。
        raise
    except requests.RequestException:
        pass
    return 0, False


def stream_segments(manager, url: str, tmp_path: str, headers: dict,
                    cancel, info, total: int, n: int,
                    base_done: int = 0, base_total: int = 0,
                    *, session_cls=None) -> int:
    """多连接「动态分块」下载：小块队列 + 谁快谁多干，慢连接会被绕过

    `base_done` / `base_total` 用于「一条轨道下完接着下另一条」的场景
    （如 DASH 先下视频再下音频）：进度按两条轨道的合计上报，不会下音频时
    进度条从 0 重新开始。

    静态切段的致命问题是**最慢的那条连接决定收尾速度**：实测 twimg 走代理
    时 6 条并发连接里 2 条只有 0.26MB/s（其余 6~7.7MB/s），静态切段下整单
    只有 2.4MB/s，而且到 80% 左右快的那几段都下完了，只剩那条慢连接在跑
    —— 用户看到的就是「前面速度很快，后面只剩几百 KB/s」。

    这里改成小块队列：谁先下完谁再拿下一块；某块跑够 CHUNK_SLOW_AFTER 秒
    仍明显慢于全场最快连接，且剩余部分按当前速度还要下载较久，才换连接
    重排。连接超时/报错同样重排（有次数上限），所以单条连接的慢与坏都只影响一个小块，
    不会再拖住收尾。
    """
    # 先确认临时文件真的能创建：失败说明是路径/权限问题，
    # 换连接、降级单连接都无济于事，必须立刻带着准确原因失败。
    ensure_temp_writable(tmp_path)
    with open(tmp_path, "wb") as f:
        f.truncate(total)

    chunk = max(SEGMENT_CHUNK_MIN,
                min(SEGMENT_CHUNK_MAX, total // max(1, n * 12)))
    pending = deque()
    pos = 0
    while pos < total:
        end = min(pos + chunk - 1, total - 1)
        pending.append({"s": pos, "e": end, "tries": 0})
        pos = end + 1
    abandon_budget = max(ABANDON_MIN_BUDGET, len(pending) // 2)

    state = {"done": 0, "err": None, "best": 0.0, "abandon": 0}
    lock = threading.Lock()
    began = time.time()
    last = [0.0, 0]                  # 上次 emit 的时间 / 字节

    manager._update_task(info, total=base_total + total, downloaded=base_done)

    chunk_count = len(pending)

    def take():
        with lock:
            return pending.popleft() if pending else None

    def requeue(item, offset):
        """把没下完的部分重新排队（已写入的字节算数，不重复下）"""
        with lock:
            item["s"] = offset
            item["tries"] += 1
            pending.append(item)

    def worker():
        session = (session_cls or requests.Session)()
        first = True
        while not cancel.is_set():
            item = take()
            if item is None:
                return
            s, e = item["s"], item["e"]
            got = 0
            t_start = time.time()
            try:
                h = dict(headers)
                h["Range"] = f"bytes={s}-{e}"
                with session.get(url, headers=h, stream=True,
                                 timeout=HTTP_TIMEOUT) as resp:
                    resp.raise_for_status()
                    if resp.status_code != 206:
                        raise ValueError(
                            f"服务端未返回分段数据（HTTP {resp.status_code}）")
                    # 每个 Range 都必须返回完全对应的区间。只检查文件大小会
                    # 放过“服务器返回了错误区间但长度恰好相同”的损坏文件。
                    range_start, range_end, range_total, range_ok = _parse_content_range(
                        resp.headers.get("Content-Range", ""))
                    expected = e - s + 1
                    if (not range_ok or range_start != s or range_end != e
                            or range_total != total):
                        raise ValueError(
                            "服务端返回的 Content-Range 与请求不一致 "
                            f"（请求 bytes={s}-{e}，响应 {resp.headers.get('Content-Range', '-')})")
                    content_length = resp.headers.get("Content-Length")
                    if content_length and content_length.isdigit() and int(content_length) != expected:
                        raise ValueError(
                            f"服务端分段长度异常（期望 {expected}，实际 {content_length}）")
                    if first:
                        # 卡住时日志里得能看出用的是哪个 CDN、切了多少块
                        first = False
                        log.info("分段连接就绪 %s：HTTP %s | %d 块 × %s 字节 "
                                 "| 总量 %s 字节 | %s",
                                 info.task_id, resp.status_code, chunk_count,
                                 chunk, total, url[:120])
                    ctype = (resp.headers.get("Content-Type") or "").lower()
                    if any(k in ctype for k in ("mpegurl", "text/",
                                                "application/json")):
                        raise ValueError(f"该地址返回的不是视频文件（{ctype}）")
                    with open(tmp_path, "r+b") as f:
                        f.seek(s)
                        for buf in resp.iter_content(CHUNK):
                            if cancel.is_set():
                                raise _Cancelled()
                            if not buf:
                                continue
                            if got + len(buf) > expected:
                                raise ValueError(
                                    f"服务端返回分段超出请求范围（期望 {expected} 字节）")
                            f.write(buf)
                            got += len(buf)
                            now = time.time()
                            # 锁序：发布锁 → 分段局部锁 → 状态锁；发信号不占后两者。
                            with manager._publish_lock:
                                snapshot = None
                                with lock:
                                    state["done"] += len(buf)
                                    done = state["done"]
                                    if now - last[0] >= 0.4:
                                        snapshot = manager._update_task(
                                            info, downloaded=base_done + done,
                                            speed=(done - last[1]) / (now - last[0]))
                                        last[0], last[1] = now, done
                                    slow = _should_reconnect_slow_chunk(
                                        got, e - s + 1, now - t_start,
                                        state["best"],
                                        state["abandon"] < abandon_budget)
                                    if slow:
                                        state["abandon"] += 1
                                if snapshot is not None:
                                    manager._events.enqueue(manager.task_progress, snapshot)
                            if snapshot is not None:
                                manager._drain_notifications()
                            if slow:
                                raise _SlowChunk()
                if got < expected:
                    raise IncompleteDownload(
                        f"分段下载不完整 {got}/{expected} 字节")
                used = max(0.001, time.time() - t_start)
                with lock:
                    state["best"] = max(state["best"], got / used)
            except _Cancelled:
                with lock:
                    if state["err"] is None:
                        state["err"] = _Cancelled()
                return
            except Exception as ex:  # noqa: BLE001
                if item["tries"] < CHUNK_MAX_ATTEMPTS and got < e - s + 1:
                    # 连接坏了或太慢：换一条连接，把剩下的部分重排
                    if isinstance(ex, _SlowChunk):
                        log.warning("分块太慢，换连接续下 %s：offset=%s 已 %.1fs "
                                    "拿到 %s/%s 字节", info.task_id, s,
                                    time.time() - t_start, got, e - s + 1)
                    else:
                        log.warning("分块中断，换连接续下 %s：offset=%s 已 %s/%s "
                                    "字节 | %s", info.task_id, s, got,
                                    e - s + 1, str(ex)[:120])
                    try:
                        session.close()
                    except Exception:  # noqa: BLE001
                        pass
                    session = (session_cls or requests.Session)()
                    requeue(item, s + got)
                    continue
                with lock:
                    if state["err"] is None:
                        state["err"] = ex
                return

    threads = [threading.Thread(target=worker, daemon=True)
               for _ in range(max(1, n))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    if cancel.is_set():
        raise _Cancelled()
    if state["err"] is not None:
        manager._update_task(info, downloaded=base_done)
        raise state["err"]
    got = state["done"]
    if got < total:
        raise IncompleteDownload(f"分段下载不完整 {got}/{total} 字节")
    manager._update_task(info, downloaded=base_done + total,
                      speed=total / max(0.001, time.time() - began))
    return total


def stream_to_file(manager, url: str, tmp_path: str, headers: dict, cancel,
                   info, base_done: int = 0, base_total: int = 0,
                   base_total_seen: int = 0, *, http_get=None) -> int:
    """把 url 流式写入 tmp_path（断点续传），返回已写入字节数

    `base_total_seen` 是探测阶段（`_probe_total`）拿到的本轨道已知总长：
    响应头里没有 Content-Length 时（chunked / `bytes x-y/*`）用它兜底，
    否则收尾的完整性判断会因为 total=0 而失效（见 BUG-1）。
    """
    downloaded = 0
    mode = "wb"
    resume_from = 0
    meta_path = tmp_path + ".meta"
    if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
        downloaded = os.path.getsize(tmp_path)
        resume_from = downloaded
        headers = dict(headers)
        headers["Range"] = f"bytes={downloaded}-"
        mode = "ab"
    with requests.get(url, headers=headers, stream=True,
                      timeout=HTTP_TIMEOUT) as resp:
        if mode == "ab" and resp.status_code == 416:
            # 本地残片已经不小于服务端资源。常见于「上次其实写完了、只是没
            # 走到 os.replace 就被中断」——此时 Range 起点越界，若直接
            # raise_for_status 会判整单失败，而且重试还是 416，永远下不完。
            # 416 的 Content-Range 形如 `bytes */12345`，能读到总长且与本地
            # 长度一致就说明残片完整，直接收尾；否则残片不可信，丢弃重下。
            total_hint = content_range_total(
                resp.headers.get("Content-Range", ""))
            if total_hint and downloaded == total_hint:
                log.info("本地残片已完整（%s 字节），直接收尾 %s",
                         downloaded, os.path.basename(tmp_path))
                return downloaded
            resp.close()
            manager._discard_partial(tmp_path, meta_path)
            manager._update_task(info, downloaded=base_done)
            log.warning("续传被拒（HTTP 416），丢弃残片重下：%s",
                        os.path.basename(tmp_path))
            raise IncompleteDownload("服务端拒绝续传（HTTP 416），已重新下载")
        resp.raise_for_status()
        log.info("请求 %s 返回 %s | 目标 %s | 续传自 %s | Content-Length=%s",
                 url[:150], resp.status_code, os.path.basename(tmp_path),
                 resume_from, resp.headers.get("Content-Length", "?"))
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if any(k in ctype for k in ("mpegurl", "text/", "application/json")):
            raise ValueError(f"该地址返回的不是视频文件（{ctype}）")
        if mode == "ab" and resp.status_code == 206:
            start, end, rng_total, ok = _parse_content_range(
                resp.headers.get("Content-Range", ""))
            if (not ok or start != resume_from
                    or (rng_total and end >= rng_total)):
                # 服务端没按我们要求的位置续传（或签名 URL 已换源）：
                # 继续追加会把两段数据拼成坏文件，必须丢弃残片重下。
                resp.close()
                manager._discard_partial(tmp_path, meta_path)
                manager._update_task(info, downloaded=base_done)
                log.warning("续传起点不符，丢弃残片重下：%s（本地 %s，服务端从 %s 开始）",
                            os.path.basename(tmp_path), resume_from, start)
                raise IncompleteDownload(
                    f"服务端未按 {resume_from} 位置续传（起点 {start}），已重新下载")
            # 206 且起点正确：本地残片可直接沿用，继续追加
            got_len = int(resp.headers.get("Content-Length", 0))
            rng_total = content_range_total(
                resp.headers.get("Content-Range", ""))
            if rng_total and rng_total != downloaded + got_len:
                # 声明的资源总长 ≠ 本地残片 + 本次余量 → 服务端资源已变
                resp.close()
                manager._discard_partial(tmp_path, meta_path)
                manager._update_task(info, downloaded=base_done)
                log.warning("服务端文件长度已变，丢弃残片重下：%s",
                            os.path.basename(tmp_path))
                raise IncompleteDownload(
                    f"服务端文件已变更（续传应得 {rng_total}，"
                    f"实为 {downloaded + got_len}），已重新下载")
            # 长度可能完全一致但内容已换源（CDN 改签名/重编码），
            # 再用 ETag 这类强标识比对一次
            rid = _resource_id(resp.headers)
            saved_rid = manager._read_meta(meta_path)
            if rid and saved_rid and rid != saved_rid:
                resp.close()
                manager._discard_partial(tmp_path, meta_path)
                manager._update_task(info, downloaded=base_done)
                log.warning("服务端资源标识已变，丢弃残片重下：%s",
                            os.path.basename(tmp_path))
                raise IncompleteDownload("服务端资源标识已变化，已重新下载")
        elif resp.status_code in (200, 206):
            # 200：服务端忽略了 Range；206：我们没请求 Range。
            # 两种情况拿到的都是全量内容，从头写入
            downloaded = 0
            mode = "wb"
        # 记录本次的强标识，供下次续传时比对
        rid = _resource_id(resp.headers)
        if rid and mode == "wb":
            manager._write_meta(meta_path, rid)
        total = int(resp.headers.get("Content-Length", 0)) + downloaded
        # 服务端没给 Content-Length（chunked 传输 / `Content-Range: bytes x-y/*`）
        # 时，用探测阶段拿到的已知总长兜底，让收尾的完整性比对仍然生效。
        # 真实 CDN（HTTP/1.1 + HTTPS）必带 CL 或 chunked（chunked 被截断时
        # requests 会抛 ChunkedEncodingError），因此这里是防御性加固：
        # 万一遇到既无长度、又靠连接关闭界定 body 的响应，至少能拿探测值比对。
        if not total and base_total_seen:
            total = base_total_seen
        manager._update_task(info, total=(base_total or 0) + total,
                          downloaded=base_done + downloaded)
        last_time, last_bytes = time.time(), downloaded
        with open(tmp_path, mode) as f:
            for chunk in resp.iter_content(CHUNK):
                if cancel.is_set():
                    raise _Cancelled()
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    now = time.time()
                    if now - last_time >= 0.4:
                        manager._update_task(
                            info, signal=manager.task_progress, downloaded=base_done + downloaded,
                            speed=(downloaded - last_bytes) / (now - last_time))
                        last_time, last_bytes = now, downloaded
    # ---- 完整性判定 ----
    # ① 知道总长：严格比对（旧行为）
    if total > 0 and downloaded < total:
        log.warning("下载被截断：%s 只拿到 %s/%s 字节", os.path.basename(tmp_path),
                    downloaded, total)
        raise IncompleteDownload(f"下载不完整 {downloaded}/{total} 字节")
    # ② 不知道总长：只能判断响应体是否被完整读完。chunked 响应在服务端正常
    #    收尾时会读到结束标记（Connection 正常关闭），而中途被切断（CDN 掉了、
    #    代理超时）时 requests 会抛 ChunkedEncodingError —— 那种情况在
    #    iter_content 里已经冒泡成异常，走不到这里。真正漏网的是「服务端主动
    #    正常 close 了连接、但只发了部分数据」：此时 Content-Length 也没有，
    #    只能靠「响应声明为压缩/分块却提前结束」这种信号，requests 无从察觉。
    #    兜底：total 未知且字节数为 0 → 判失败，别把空文件当成品。
    if total <= 0 and downloaded <= 0:
        raise IncompleteDownload("服务端未返回任何数据（且未声明长度），已放弃")
    # ③ 既没有 Content-Length、探测也没拿到总长：传输层无从判断完整性。
    #    实测「服务端发 500000 字节后直接 close、不声明长度」时 iter_content
    #    不会抛异常，旧实现把半截文件当成品落盘并标「已完成」。这种响应
    #    只能靠实体是否完整收尾来判：Content-Length 在、或给了 chunked
    #    终止块，二者都没有时视为不可信，明确失败而不是静默产出坏文件。
    if total <= 0 and downloaded > 0 and not _body_definitely_complete(resp):
        log.warning("响应未声明长度且连接提前关闭：%s 只拿到 %s 字节",
                    os.path.basename(tmp_path), downloaded)
        raise IncompleteDownload(
            f"服务端未声明文件长度且连接提前中断（已收到 {downloaded} 字节），"
            "无法确认文件完整，已放弃本次下载")
    return downloaded
