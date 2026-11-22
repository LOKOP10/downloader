# -*- coding: utf-8 -*-
"""下载任务管理：多线程队列 + Qt 信号汇报进度"""
import os
import re
import subprocess
import threading
import time
import urllib.parse
import uuid
from collections import deque

import requests
from PySide6.QtCore import QObject, Signal

from .applog import get_logger
from .domain import needs_cookie_for_url
from .http_headers import HEADERS, headers_for
from .models import DownloadTaskInfo, clean_cookie
from . import download_transport as _transport
from .download_transport import (  # noqa: F401 - 保留对外及测试兼容导出
    ABANDON_MIN_BUDGET,
    CHUNK,
    CHUNK_MAX_ATTEMPTS,
    CHUNK_MIN_REMAINING_TIME,
    CHUNK_SLOW_AFTER,
    CHUNK_SLOW_RATIO,
    CONNECT_TIMEOUT,
    CONN_BUDGET,
    FFMPEG_TIMEOUT,
    HTTP_TIMEOUT,
    IncompleteDownload,
    MissingDependency,
    PROBE_TIMEOUT,
    READ_TIMEOUT,
    SEGMENT_CHUNK_MAX,
    SEGMENT_CHUNK_MIN,
    SEGMENT_DEFAULT_CONNECTIONS,
    SEGMENT_ENABLED,
    SEGMENT_MAX,
    SEGMENT_MIN_SIZE,
    SIGNED_URL_TTL_SECONDS,
    SignedUrlExpired,
    TEMP_PATH_SUFFIXES,
    _Cancelled,
    _SlowChunk,
    _body_definitely_complete,
    _parse_content_range,
    _resource_id,
    _should_reconnect_slow_chunk,
    content_range_total,
    ensure_temp_writable,
    prepare_target_path,
    signed_url_expired,
    signed_url_issued_at,
)
from .task_events import TaskEventBus

log = get_logger("download")

class DownloadManager(QObject):
    """队列及任务状态的所有者。公开查询、add 返回值和信号均交付独立字段快照。

    _lock 保护任务字段事务与集合查询：一次 all_tasks 的全部字段取自同一临界区。
    写入先取 _publish_lock 再取 _lock，网络、文件处理、转码和 Qt 发信号都不占
    _lock。通知的入队与发送由 TaskEventBus（task_events.py）负责：按事务产生顺序
    排队，同线程槽内取消/重试产生的新通知延后发送，Qt QueuedConnection 接收方
    因此按发布顺序收到稳定的历史值。
    """
    task_added = Signal(object)                       # DownloadTaskInfo
    task_progress = Signal(object)                    # DownloadTaskInfo
    task_finished = Signal(object)                    # DownloadTaskInfo

    def __init__(self, max_concurrent: int = 3, parent=None,
                 segment_enabled: bool = True,
                 segment_connections: int = SEGMENT_DEFAULT_CONNECTIONS):
        super().__init__(parent)
        self.max_concurrent = max(1, max_concurrent)
        self._segment_enabled = bool(segment_enabled)
        # 每文件连接数（IDM 式）：设置页可改，1-32
        self._segment_connections = self._clamp_connections(segment_connections)
        self._tasks = {}          # task_id -> DownloadTaskInfo
        self._cancel_flags = {}   # task_id -> threading.Event
        self._cookies = {}        # task_id -> Cookie 字符串（用于需要登录态的平台）
        # task_id -> 有序尝试序列 [(视频地址, 音频地址, [音频备用地址]), ...]
        self._plans = {}
        self._queue = []
        self._active = 0
        self._lock = threading.Lock()
        self._publish_lock = threading.RLock()
        self._events = TaskEventBus()
        self._watchdog = None     # 卡住检测线程（第一次入队时启动）
        self._stall_seen = {}     # task_id -> (字节数, 该字节数最后一次变化的时间)

    def _drain_notifications(self):
        """按事务入队顺序发通知；分派期间不持任何管理器锁。"""
        self._events.drain()

    def _update_task(self, info, *, signal=None, **changes):
        """一组相关字段和快照在同一状态锁内提交，通知在锁外按顺序发出。"""
        with self._publish_lock:
            with self._lock:
                for name, value in changes.items():
                    setattr(info, name, value)
                snapshot = info.snapshot()
            if signal is not None:
                self._events.enqueue(signal, snapshot)
        if signal is not None:
            self._drain_notifications()
        return snapshot

    def _finish_task(self, info):
        with self._publish_lock:
            with self._lock:
                info.speed = 0
                self._active -= 1
                self._cancel_flags.pop(info.task_id, None)
                # Cookie 和候选地址保留到删除记录；完成通知前允许按 ID 重试。
                snapshot = info.snapshot()
            self._events.enqueue(self.task_finished, snapshot)
        self._drain_notifications()

    # ------------------------------------------------------------------ #
    def add(self, url: str, save_path: str, name: str,
            fallbacks: list = None, cookies: str = "",
            audio_url: str = "", quality: str = "",
            audio_map: dict = None, url_backups: dict = None,
            audio_backups: dict = None,
            cover_url: str = "",
            convert_to_gif: bool = False,
            gif_path: str = "") -> DownloadTaskInfo:
        plan = self._build_plan(url, fallbacks, audio_url, audio_map,
                                url_backups, audio_backups)
        with self._publish_lock:
            save_path = self._unique_path(save_path)
            gif_path = self._unique_path(gif_path) if gif_path else ""
            info = DownloadTaskInfo(task_id=uuid.uuid4().hex[:12], url=url,
                                    save_path=save_path, name=name,
                                    quality=quality or "",
                                    cover_url=cover_url or "",
                                    convert_to_gif=bool(convert_to_gif),
                                    gif_path=gif_path)
            with self._lock:
                self._tasks[info.task_id] = info
                self._cookies[info.task_id] = cookies or ""
                self._plans[info.task_id] = plan
                self._queue.append(info.task_id)
                snapshot = info.snapshot()
            log.info("入队 %s | %s | 画质=%s | 候选=%d | 音频轨=%s | 保存到 %s",
                     info.task_id, url, quality or "-",
                     len(plan),
                     "有" if audio_url else "无", save_path)
            self._events.enqueue(self.task_added, snapshot.snapshot())
        self._drain_notifications()
        self._ensure_watchdog()
        self._pump()
        return snapshot

    @staticmethod
    def _build_plan(url: str, fallbacks, audio_url: str, audio_map: dict,
                    url_backups: dict, audio_backups: dict) -> list:
        """把「主地址 + 同档备用 CDN + 其他清晰度」摊平成有序尝试序列

        每一项是 (视频地址, 音频地址, [音频备用地址])。同一档位先试主地址，再试
        它的备用地址——B站 baseUrl 挂在 PCDN 节点（cn-jsnj-fx-*）上，实测经常
        TLS 重置或直接连不上，而 backupUrl 的 upos-* 镜像稳定可用；只认主地址
        时整单会一直卡在 0%。一档全挂才降到下一档画质，且每档自带它的音频轨
        （旧逻辑只有第一档带音频，降档后下出来的是无声视频）。
        """
        url_backups = url_backups or {}
        audio_backups = audio_backups or {}
        audio_map = audio_map or {}
        plan, seen = [], set()

        def push(video: str, audio: str = ""):
            key = (video, audio)
            if not video or key in seen:
                return
            seen.add(key)
            plan.append((video, audio,
                         [a for a in (audio_backups.get(audio) or [])
                          if a and a != audio]))

        for q_url in [url] + list(fallbacks or []):
            if not q_url:
                continue
            audio = audio_map.get(q_url, "") or (audio_url if q_url == url else "")
            push(q_url, audio)
            for alt in (url_backups.get(q_url) or []):
                push(alt, audio)
        return plan

    def _unique_path(self, save_path: str) -> str:
        """同一秒内文件名相同时自动追加 _1/_2 后缀，避免互相覆盖"""
        if not os.path.exists(save_path) and not self._path_in_use(save_path):
            return save_path
        root, ext = os.path.splitext(save_path)
        i = 1
        while True:
            candidate = f"{root}_{i}{ext}"
            if not os.path.exists(candidate) and not self._path_in_use(candidate):
                return candidate
            i += 1

    def _path_in_use(self, save_path: str) -> bool:
        with self._lock:
            # 「合并中」的 DASH 任务此刻还没写出成品，但最终目标就是 save_path，
            # ffmpeg 会直接往那儿写；漏掉它会让同一秒的第二次下载复用同一路径。
            return any(t.save_path == save_path
                       and t.status in ("等待中", "下载中", "合并中", "转换中")
                       for t in self._tasks.values())

    def cancel(self, task_id: str):
        self._cancel_task(task_id)

    def _cancel_task(self, task_id):
        with self._publish_lock:
            notifications = []
            with self._lock:
                info = self._tasks.get(task_id)
                if info is None or info.status not in ("等待中", "下载中", "合并中", "转换中"):
                    return False
                flag = self._cancel_flags.get(task_id)
                if flag is not None:
                    flag.set()
                elif info.status == "等待中":
                    info.status = "已取消"
                    if task_id in self._queue:
                        self._queue.remove(task_id)
                    notifications.append((self.task_progress, info.snapshot()))
                else:
                    return False
            self._events.enqueue_many(notifications)
        self._drain_notifications()
        return True

    def retry(self, task_id: str, rebase_to: str = ""):
        """重试一个失败/已取消的任务。

        `rebase_to` 是**当前**的下载根目录：给了它就把任务重新挂到该目录下，
        用原文件名。这样用户「改了下载目录再点重试」才会落到新目录 ——
        否则 save_path 是任务创建时就固定的，永远写回旧目录，
        表现就是「目录改了但重试还下到老地方」（实测复现于失败任务的批量重试）。
        """
        if self._retry_tasks([task_id], self.FINISHED_STATUS, rebase_to):
            self._pump()

    def all_tasks(self):
        """在一次状态锁内取得全部任务的独立字段快照，返回顺序仍为入队顺序。"""
        with self._lock:
            return [info.snapshot() for info in self._tasks.values()]

    # 已结束（不会再有进度更新）的状态集合
    FINISHED_STATUS = ("已完成", "已取消", "失败")

    # 「卡住」判定：任务在 STALL_WARN_AFTER 秒内一个字节都没涨，就写一条 WARNING
    # （下载线程还在跑但没有任何进展——事后查日志能直接看出卡在哪一步）
    STALL_WARN_AFTER = 60
    STALL_CHECK_INTERVAL = 15

    def finished_tasks(self, statuses=FINISHED_STATUS):
        """文件处理与收尾已完成、活动标记已释放的终态任务快照。

        活动标记仍在的任务不返回：此时工作线程可能还在清理产物，不能提前删除或重试。
        """
        with self._lock:
            return [info.snapshot() for info in self._tasks.values()
                    if info.status in statuses and info.task_id not in self._cancel_flags]

    def retry_tasks(self, tasks=None, rebase_to: str = ""):
        """批量重试；tasks 为空时重试所有失败任务。返回实际重试的任务数

        `rebase_to` 语义见 [retry]：把任务重新挂到当前下载目录下。
        """
        ids = None if tasks is None else [task.task_id for task in tasks]
        count = self._retry_tasks(ids, ("失败",), rebase_to)
        if count:
            self._pump()
        return count

    def _retry_tasks(self, task_ids, statuses, rebase_to):
        with self._publish_lock:
            with self._lock:
                ids = list(self._tasks) if task_ids is None else list(dict.fromkeys(task_ids))
            notifications = []
            for task_id in ids:
                with self._lock:
                    info = self._tasks.get(task_id)
                    if (info is None or info.status not in statuses
                            or task_id in self._cancel_flags):
                        continue
                    before = info.snapshot()
                # 文件系统探测不占状态锁；发布锁阻止其他控制事务抢占重试目标。
                save_path, gif_path = before.save_path, before.gif_path
                if rebase_to:
                    candidate = os.path.join(rebase_to, os.path.basename(save_path))
                    if os.path.dirname(os.path.abspath(candidate)) != \
                            os.path.dirname(os.path.abspath(save_path)):
                        save_path = self._unique_path(candidate)
                        if before.convert_to_gif and gif_path:
                            gif_path = self._unique_path(os.path.join(
                                os.path.dirname(save_path), os.path.basename(gif_path)))
                with self._lock:
                    info.save_path = save_path
                    info.gif_path = gif_path
                    info.status = "等待中"
                    info.error = ""
                    info.downloaded = 0
                    info.speed = 0
                    self._queue.append(task_id)
                    notifications.append((self.task_progress, info.snapshot()))
                if save_path != before.save_path:
                    log.info("重试 %s 改为新目录：%s", task_id, save_path)
            self._events.enqueue_many(notifications)
        self._drain_notifications()
        return len(notifications)

    def cancel_tasks(self, tasks=None):
        """批量取消；tasks 为空时取消所有未结束任务。返回实际取消的任务数"""
        with self._lock:
            ids = list(self._tasks) if tasks is None else list(dict.fromkeys(
                task.task_id for task in tasks))
        return sum(self._cancel_task(task_id) for task_id in ids)

    def remove_tasks(self, task_ids) -> int:
        """从任务表移除记录（只清列表，不删已下载的文件）。

        只移除 `status in FINISHED_STATUS` 的任务，正在跑的任务会被跳过，
        避免下载线程回调时找不到 task_id。
        """
        removed = 0
        with self._publish_lock:
            with self._lock:
                for tid in list(task_ids):
                    info = self._tasks.get(tid)
                    if (info is None or info.status not in self.FINISHED_STATUS
                            or tid in self._cancel_flags):
                        continue
                    self._tasks.pop(tid, None)
                    self._cookies.pop(tid, None)
                    self._plans.pop(tid, None)
                    self._queue = [queued for queued in self._queue if queued != tid]
                    removed += 1
        return removed

    def clear_finished(self) -> int:
        """清除所有已结束的任务记录，返回清除条数"""
        return self.remove_tasks([t.task_id for t in self.finished_tasks()])

    def _pump(self):
        with self._lock:
            while self._active < self.max_concurrent and self._queue:
                task_id = self._queue.pop(0)
                info = self._tasks.get(task_id)
                if info is None or info.status != "等待中":
                    continue
                self._active += 1
                self._cancel_flags[task_id] = threading.Event()
                t = threading.Thread(target=self._run, args=(task_id,), daemon=True)
                t.start()

    def _run(self, task_id: str):
        with self._lock:
            info = self._tasks[task_id]
            cancel = self._cancel_flags[task_id]
            cookies = self._cookies.get(task_id, "")
            plan = self._plans.get(task_id) or [(info.url, "", [])]
        t0 = time.time()
        log.info("开始下载 %s | %s | 画质=%s | 候选=%d",
                 task_id, info.url, info.quality or "-",
                 len(plan))
        if (info.url or "").startswith("jmcomic://"):
            self._run_jmcomic(info, cancel, cookies)
            log.info("下载结束 %s 状态=%s 用时%.1fs", task_id, info.status,
                     time.time() - t0)
            self._finish_task(info)
            self._pump()
            return
        # 依次尝试「主地址 → 同档备用 CDN → 下一档画质」，全部失败才标记失败
        last_err = ""
        bad_hosts = set()
        if not hasattr(self, "_bad_hosts_by_task"):
            self._bad_hosts_by_task = {}
        self._bad_hosts_by_task[task_id] = bad_hosts
        try:
            for idx, (url, audio_url, audio_alts) in enumerate(plan):
                if cancel.is_set():
                    break
                if idx > 0:  # 换地址时清掉上一个地址的残片，避免错误续传
                    self._cleanup_temp_files(info.save_path)
                success = False
                # 同一地址最多 3 次（截断时用 .part 续传重试）
                for _ in range(3):
                    if cancel.is_set():
                        break
                    try:
                        self._download_once(info, url, cancel, cookies,
                                            audio_url, audio_alts)
                        last_err = ""
                        self._update_task(info, error="")
                        success = True
                        break
                    except _Cancelled:
                        last_err = ""
                        break
                    except IncompleteDownload as e:
                        last_err = str(e)[:200]
                        self._update_task(info, error=last_err)
                        log.warning("下载不完整，重试 %s（地址 %d/%d）：%s",
                                    task_id, idx + 1, len(plan), e)
                        continue
                    except (MissingDependency, ValueError, SignedUrlExpired) as e:
                        # 参数/依赖类错误（如缺 ffmpeg、地址无效、链接过期）换其他地址
                        # 也一样，直接判定失败，不要浪费 3 次重试 + 剩余候选地址。
                        # 链接过期尤其要在这里拦住：实测 15 个候选地址会全部 403，
                        # 既浪费时间又让日志里刷满无意义的 403。
                        last_err = str(e)[:200]
                        self._update_task(info, error=last_err)
                        log.error("下载失败（不可重试）%s：%s", task_id, e,
                                  exc_info=True)
                        break
                    except Exception as e:  # noqa: BLE001 - 换备用地址再试
                        if isinstance(e, requests.RequestException):
                            host = (urllib.parse.urlparse(url).hostname or "").lower()
                            if host:
                                bad_hosts.add(host)
                        last_err = str(e)[:200]
                        self._update_task(info, error=last_err)
                        log.error("下载出错 %s（地址 %d/%d）：%s",
                                  task_id, idx + 1, len(plan), e, exc_info=True)
                        break
                if success or cancel.is_set():
                    break
        finally:
            self._bad_hosts_by_task.pop(task_id, None)
        if cancel.is_set():
            self._update_task(info, status="已取消")
        elif last_err:
            self._update_task(info, status="失败", error=last_err)
        elif info.convert_to_gif and info.status in ("已完成", "转换中"):
            # 抖音图文动图：视频轨下载完成后立刻转 GIF，任务最终产物是 .gif
            self._convert_task_gif(info, cancel)
        # 只要任务没有真正成功，就清掉可能残留的临时文件，
        # 避免用户下载目录里堆 .part / .meta
        if info.status != "已完成":
            self._cleanup_temp_files(info.save_path)
        log.info("下载结束 %s 状态=%s 用时%.1fs 字节=%s/%s 错误=%s",
                 task_id, info.status, time.time() - t0,
                 info.downloaded, info.total, info.error or "-")
        self._finish_task(info)
        self._pump()

    # ------------------------------------------------------------------ #
    # 动图转 GIF
    def _convert_task_gif(self, info, cancel):
        """把已下载的动图视频轨转成 GIF；失败保留 MP4 供用户手动处理"""
        from .mp4_to_gif import ConversionCancelled, ConversionError, convert

        source = info.save_path
        target = info.gif_path or (os.path.splitext(source)[0] + ".gif")
        self._update_task(info, signal=self.task_progress, status="转换中", error="")
        log.info("开始转 GIF %s | %s -> %s", info.task_id, source, target)
        try:
            convert(source, target, cancel=cancel)
        except ConversionCancelled:
            self._update_task(info, signal=self.task_progress, status="已取消", error="")
            log.info("已取消转 GIF %s，保留视频 %s", info.task_id, source)
            return
        except ConversionError as exc:
            self._update_task(info, signal=self.task_progress, status="失败",
                              error=f"GIF 转换失败：{exc}"[:300])
            log.error("转 GIF 失败 %s：%s", info.task_id, exc)
            return
        except Exception as exc:  # noqa: BLE001 - 转码异常统一落到任务失败
            self._update_task(info, signal=self.task_progress, status="失败",
                              error=f"GIF 转换失败：{exc}"[:300])
            log.exception("转 GIF 异常 %s", info.task_id)
            return
        # 转换成功后删除临时视频，任务路径与名称都指向 GIF
        try:
            os.remove(source)
        except OSError as exc:
            log.warning("GIF 已生成但删除临时视频失败 %s：%s", source, exc)
        changes = {"save_path": target, "status": "已完成"}
        try:
            size = os.path.getsize(target)
            changes.update(downloaded=size, total=size)
        except OSError:
            pass
        self._update_task(info, signal=self.task_progress, **changes)
        log.info("转 GIF 完成 %s | %s（%s 字节）", info.task_id, target, info.total)

    # ------------------------------------------------------------------ #
    # 卡住检测
    def _ensure_watchdog(self):
        if self._watchdog is not None:
            return
        self._watchdog = threading.Thread(target=self._watch_stalls,
                                          name="stall-watch", daemon=True)
        self._watchdog.start()

    def _watch_stalls(self):
        """盯着「一个字节都不涨」的任务：卡住时日志里要留下时间/URL/画质/字节数

        用户能看到的只有界面上「不动了」，这里把当时的现场写进 app.log，
        事后定位不必再靠复现。
        """
        while True:
            time.sleep(self.STALL_CHECK_INTERVAL)
            try:
                now = time.time()
                alive = set()
                for t in self.all_tasks():
                    if t.status not in ("下载中", "合并中"):
                        self._stall_seen.pop(t.task_id, None)
                        continue
                    alive.add(t.task_id)
                    got, ts = self._stall_seen.get(t.task_id, (t.downloaded, now))
                    if t.downloaded != got:
                        self._stall_seen[t.task_id] = (t.downloaded, now)
                    elif now - ts >= self.STALL_WARN_AFTER:
                        log.warning("任务疑似卡住 %s：已 %ds 无字节增长 | 状态=%s "
                                    "进度=%s/%s 画质=%s | %s",
                                    t.task_id, int(now - ts), t.status,
                                    t.downloaded, t.total, t.quality or "-",
                                    t.url)
                        self._stall_seen[t.task_id] = (got, now)
                for tid in list(self._stall_seen):
                    if tid not in alive:
                        self._stall_seen.pop(tid, None)
            except Exception:  # noqa: BLE001 —— 看门狗自己不能把程序带崩
                log.exception("卡住检测线程异常")

    # ------------------------------------------------------------------ #
    # 临时文件
    TEMP_SUFFIXES = (".part", ".vpart", ".apart", ".mpart",
                     ".part.meta", ".vpart.meta", ".apart.meta", ".mpart.meta")

    def _cleanup_temp_files(self, save_path: str) -> int:
        """清除某个保存路径对应的所有临时/指纹文件，返回清除数量"""
        n = 0
        for suffix in self.TEMP_SUFFIXES:
            path = save_path + suffix
            try:
                if os.path.exists(path):
                    os.remove(path)
                    n += 1
            except OSError:
                pass
        return n

    def temp_files(self) -> list:
        """当前存在的临时文件列表（供自检/清理用）"""
        return [t.save_path + s for t in self.all_tasks()
                for s in self.TEMP_SUFFIXES
                if os.path.exists(t.save_path + s)]

    def cleanup_all_temp(self) -> int:
        """清理所有任务的临时文件，返回清除数量"""
        return sum(self._cleanup_temp_files(t.save_path)
                   for t in self.all_tasks())

    @staticmethod
    def _discard_partial(tmp_path: str, meta_path: str = ""):
        """丢弃残片与指纹文件（续传不可信时调用）"""
        for path in (tmp_path, meta_path or (tmp_path + ".meta")):
            try:
                if path and os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass

    @staticmethod
    def _read_meta(meta_path: str) -> str:
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""

    @staticmethod
    def _write_meta(meta_path: str, value: str):
        try:
            with open(meta_path, "w", encoding="utf-8") as f:
                f.write(value)
        except OSError:
            pass

    @staticmethod
    def _clamp_connections(value) -> int:
        """把连接数收敛到 1-32（IDM 的取值区间），非法值回退到默认 8"""
        try:
            n = int(value)
        except (TypeError, ValueError):
            return SEGMENT_DEFAULT_CONNECTIONS
        return max(1, min(SEGMENT_MAX, n))

    def set_connections(self, value) -> int:
        """设置页实时改每文件连接数；返回落地后的值"""
        self._segment_connections = self._clamp_connections(value)
        return self._segment_connections

    def _segment_count(self, total: int) -> int:
        """按文件大小与当前并发数决定分段数（1 = 不分片）

        IDM 语义：用户设几条连接就给几条。只有全局预算吃紧时（并发任务太多，
        连接总数会爆）才按比例摊薄，避免一次开出几百个 socket。
        """
        if not getattr(self, "_segment_enabled", True):
            return 1
        if total < SEGMENT_MIN_SIZE:
            return 1
        per_file = self._clamp_connections(
            getattr(self, "_segment_connections", SEGMENT_DEFAULT_CONNECTIONS))
        with self._lock:
            active = max(1, self._active)
        budget = max(1, CONN_BUDGET // active)
        return max(1, min(per_file, budget))

    @staticmethod
    def _probe_total(url: str, headers: dict) -> tuple:
        """探测资源总长度与是否支持分段，返回 (total, 是否支持 Range)"""
        return _transport.probe_total(url, headers, http_get=requests.get)

    def _stream_segments(self, url: str, tmp_path: str, headers: dict,
                         cancel, info, total: int, n: int,
                         base_done: int = 0, base_total: int = 0) -> int:
        """多连接动态分块下载，委托至纯 Python 传输模块。"""
        return _transport.stream_segments(
            self, url, tmp_path, headers, cancel, info, total, n,
            base_done=base_done, base_total=base_total,
            session_cls=requests.Session,
        )

    def _fetch_track(self, url: str, tmp_path: str, headers: dict, cancel,
                     info, base_done: int = 0, base_total: int = 0) -> int:
        """下载一条轨道（视频或音频）：能分段就 IDM 式多连接，否则单连接。

        返回本次写入 tmp_path 的字节数。分段走整块重写（预分配全尺寸），
        失败时清掉残片再降级单连接，避免两种写法的残片互相污染。
        """
        total, can_range = self._probe_total(url, headers)
        n = self._segment_count(total) if (can_range and total > 0) else 1
        if n > 1:
            log.info("轨道分段 %s：%s 字节 | 连接数=%d | %s",
                     info.task_id, total, n, url[:120])
            try:
                self._stream_segments(url, tmp_path, headers, cancel, info,
                                      total, n, base_done=base_done,
                                      base_total=base_total)
                return total
            except _Cancelled:
                raise
            except Exception:  # noqa: BLE001 - 降级到单连接重下
                # 分片文件是预分配全尺寸的，必须清掉，不能被续传逻辑接管
                log.warning("分段下载失败，降级单连接重下 %s（%s）",
                            info.task_id, url[:120], exc_info=True)
                self._discard_partial(tmp_path)
        return self._stream_to_file(url, tmp_path, headers, cancel, info,
                                    base_done=base_done, base_total=base_total,
                                    base_total_seen=total)

    def _stream_to_file(self, url: str, tmp_path: str, headers: dict, cancel,
                        info, base_done: int = 0, base_total: int = 0,
                        base_total_seen: int = 0) -> int:
        """单连接流式断点续传下载，委托至纯 Python 传输模块。"""
        return _transport.stream_to_file(
            self, url, tmp_path, headers, cancel, info,
            base_done=base_done, base_total=base_total,
            base_total_seen=base_total_seen, http_get=requests.get,
        )

    def _run_jmcomic(self, info, cancel, cookies: str):
        """禁漫本子走 jmcomic 解码下载（原图切片混淆，不能当普通直链）。"""
        from .jmcomic_bridge import DownloadCancelled, download_jmcomic
        self._update_task(info, signal=self.task_progress, status="下载中")

        def on_progress(done: int, total: int):
            done = max(0, int(done))
            self._update_task(info, signal=self.task_progress, downloaded=done,
                              total=max(int(total), done), status="下载中")

        try:
            download_jmcomic(info.url, info.save_path, cookies,
                             cancel=cancel, progress_cb=on_progress)
            if cancel.is_set():
                self._update_task(info, signal=self.task_progress, status="已取消", speed=0)
                return
            total = info.total if info.total > 0 else max(info.downloaded, 1)
            self._update_task(info, total=total, downloaded=total, status="已完成")
        except DownloadCancelled:
            self._update_task(info, status="已取消")
        except Exception as e:  # noqa: BLE001
            self._update_task(info, status="失败", error=str(e)[:200])
            # 只记录异常栈，不记录Cookie；此前这里只改状态导致日志看不见失败原因。
            log.exception("禁漫下载失败 %s：%s", info.task_id, info.error)
        self._update_task(info, signal=self.task_progress, speed=0)

    def _download_once(self, info, url: str, cancel, cookies: str = "",
                       audio_url: str = "", audio_fallbacks: list = None):
        """下载单个地址；有 audio_url 时先分别下载视频/音频再 ffmpeg 合并

        `audio_fallbacks` 是这路音频的其他 CDN 地址，按顺序作为兜底。
        """
        self._update_task(info, signal=self.task_progress, status="下载中")
        # 规范化路径 + 提前暴露「父目录建不出来 / 位置被同名目录占用 / 不可写」，
        # 避免把 Errno 13 这种含义模糊的底层异常直接抛到界面上。
        self._update_task(info, save_path=prepare_target_path(info.save_path))

        # ⚠️ 抖音直链是时效签名：过期后**所有**镜像统一回 403。
        # 实测 17:29 解析、21:17 重试 → 15 个候选地址全部 403，而用户只看到
        # 一句「403 Forbidden」，完全不知道是链接过期，只能反复点重试。
        # 这里在下载前先判断，过期就明确说「请重新解析」，别白跑 15 个地址。
        if signed_url_expired(url):
            raise SignedUrlExpired(
                "下载链接已过期（抖音直链是限时签名，通常 30 分钟内有效）。"
                "请重新粘贴分享链接并解析后再下载。")

        def _headers(u):
            h = headers_for(u)
            # 只有 IG / B站 的 CDN 需要 Cookie（抖音 CDN 带 Cookie 反而更易被风控）
            if cookies and needs_cookie_for_url(u):
                # Cookie 必须作为单个请求头发送；把每个 cookie 拆成独立
                # header 会被 requests/服务器当成未知头，登录态不会生效。
                h["Cookie"] = clean_cookie(cookies)
            return h

        if audio_url:
            from .ffmpeg import ffmpeg_path
            ff = ffmpeg_path()
            if not ff:
                # 抛出可识别异常：换备用地址也无济于事，直接判定失败
                raise MissingDependency(
                    "缺少 ffmpeg 合并组件，无法合并该清晰度的音视频。"
                    "请到「设置」中下载组件后重试，或改用较低清晰度")
            v_tmp = info.save_path + ".vpart"
            a_tmp = info.save_path + ".apart"
            # 视频轨是 DASH 下的大头，同样走 IDM 式多连接（旧实现只有单连接）
            v_got = self._fetch_track(url, v_tmp, _headers(url), cancel, info)
            v_total = info.total or v_got
            # 音频轨同样可能挂在坏 CDN 上：自带备用地址，优先避开本次已超时的坏主机
            audio_urls = [audio_url] + [u for u in (audio_fallbacks or [])
                                        if u and u != audio_url]
            bad_hosts = getattr(self, "_bad_hosts_by_task", {}).get(info.task_id) or set()
            if bad_hosts and len(audio_urls) > 1:
                audio_urls.sort(
                    key=lambda u: 1 if (urllib.parse.urlparse(u).hostname or "").lower() in bad_hosts else 0
                )
            a_got, audio_err = 0, None
            for i, a_url in enumerate(audio_urls):
                try:
                    a_got = self._fetch_track(a_url, a_tmp, _headers(a_url),
                                              cancel, info, base_done=v_got,
                                              base_total=v_total)
                    audio_err = None
                    break
                except _Cancelled:
                    raise
                except Exception as e:  # noqa: BLE001 - 换备用音频地址
                    audio_err = e
                    log.warning("音频轨下载失败 %s（地址 %d/%d）：%s",
                                info.task_id, i + 1, len(audio_urls), e)
                    if i + 1 < len(audio_urls):
                        # 换 CDN 不续传：不同签名的地址不能拼在同一份残片上
                        self._discard_partial(a_tmp)
                        self._update_task(info, downloaded=v_got)
            if audio_err is not None:
                raise audio_err
            log.info("DASH 两轨下载完成 %s：视频 %s 字节 + 音频 %s 字节，开始合并",
                     info.task_id, v_got, a_got)
            self._update_task(info, signal=self.task_progress, status="合并中")
            # -nostdin + DEVNULL：打包成窗口程序后没有控制台，ffmpeg 若去读
            # stdin 会一直等（任务永远停在「合并中」）
            cmd = [ff, "-nostdin", "-y", "-loglevel", "error", "-i", v_tmp,
                   "-i", a_tmp, "-c", "copy", info.save_path]
            log.info("合并 %s：%s", info.task_id, " ".join(cmd))
            t_merge = time.time()
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT,
                    stdin=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except subprocess.TimeoutExpired:
                # 合并卡死不能把任务永远挂住：超时直接判失败并清掉半成品
                self._cleanup_temp_files(info.save_path)
                self._discard_partial(info.save_path)
                raise ValueError(f"ffmpeg 合并超过 {FFMPEG_TIMEOUT}s 未结束，已中止")
            log.info("合并结束 %s：返回码 %s 用时 %.1fs%s", info.task_id,
                     proc.returncode, time.time() - t_merge,
                     (" stderr=" + (proc.stderr or "").strip()[:300])
                     if proc.returncode != 0 else "")
            if proc.returncode != 0 or not os.path.exists(info.save_path):
                # 失败时 ffmpeg 往往已经在目标路径留下一个截断的 mp4，文件名却
                # 和正常成品一模一样 —— 不删掉，用户会以为下全了（要等到重试
                # 才会被覆盖）。超时分支同样处理，两处保持一致。
                self._cleanup_temp_files(info.save_path)
                self._discard_partial(info.save_path)
                raise ValueError(f"ffmpeg 合并失败：{(proc.stderr or '')[:150]}")
            self._cleanup_temp_files(info.save_path)
            self._update_task(info, downloaded=info.total or (v_got + a_got),
                              status="转换中" if info.convert_to_gif else "已完成")
            return

        # 单文件：IDM 式多连接分段（能分段就分段，失败自动降级单连接）
        tmp_path = info.save_path + ".part"
        got = self._fetch_track(url, tmp_path, _headers(url), cancel, info)
        if info.save_path.lower().endswith(".mp4") and got < 30 * 1024:
            self._cleanup_temp_files(info.save_path)
            raise ValueError(f"文件仅 {got} 字节，疑似分片或错误页面，已换用其他清晰度")
        os.replace(tmp_path, info.save_path)
        # 收尾：清掉 .part.meta 之类不再需要的附属文件
        self._cleanup_temp_files(info.save_path)
        self._update_task(info, downloaded=info.total or got,
                          status="转换中" if info.convert_to_gif else "已完成")
