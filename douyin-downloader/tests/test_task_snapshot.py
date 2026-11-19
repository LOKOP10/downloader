"""任务快照契约：隔离后台写入、锁内一致取值、锁外有序 Qt 通知。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QObject, Qt, Slot
from PySide6.QtWidgets import QApplication

from app.core.downloader import DownloadManager, MissingDependency
from app.core.models import DownloadTaskInfo


class Receiver(QObject):
    def __init__(self):
        super().__init__()
        self.values = []

    @Slot(object)
    def record(self, info):
        self.values.append(info)


class TaskSnapshotTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="_tmp_task_snapshot_")
        self.root = Path(self.temp.name)
        self.manager = DownloadManager(segment_enabled=False)
        self.patches = [patch.object(self.manager, "_pump"),
                        patch.object(self.manager, "_ensure_watchdog")]
        for current in self.patches:
            current.start()
        self.threads = []
        self.releases = []

    def tearDown(self):
        for event in self.releases:
            event.set()
        for thread, _ in self.threads:
            thread.join(3)
        for current in reversed(self.patches):
            current.stop()
        self.app.processEvents()
        self.temp.cleanup()

    def event(self):
        event = threading.Event()
        self.releases.append(event)
        return event

    def start(self, fn):
        errors = []
        def run():
            try:
                fn()
            except BaseException as error:
                errors.append(error)
        thread = threading.Thread(target=run, daemon=True)
        self.threads.append((thread, errors))
        thread.start()
        return thread, errors

    def join(self, job):
        thread, errors = job
        thread.join(3)
        self.assertFalse(thread.is_alive(), "任务线程未按约定结束，可能存在锁重入或锁序死锁")
        if errors:
            raise errors[0]

    def task(self, status="等待中", **options):
        view = self.manager.add("https://example.invalid/video", str(self.root / "video.mp4"),
                                "测试视频", **options)
        info = self.manager._tasks[view.task_id]
        if status != "等待中":
            with self.manager._lock:
                self.manager._queue.remove(info.task_id)
            self.manager._update_task(info, status=status)
        return view, info

    def activate(self, info):
        with self.manager._lock:
            self.manager._queue = [tid for tid in self.manager._queue if tid != info.task_id]
            self.manager._cancel_flags[info.task_id] = threading.Event()
            self.manager._active += 1

    def test_public_values_are_detached_from_internal_and_later_updates(self):
        added = []
        self.manager.task_added.connect(added.append)
        returned, info = self.task()
        before = self.manager.all_tasks()[0]
        self.assertIsInstance(returned, DownloadTaskInfo)
        self.assertIsNot(returned, info)
        self.assertIsNot(added[0], info)
        self.assertIsNot(returned, added[0])
        self.manager._update_task(info, status="失败", error="synthetic", total=10, downloaded=5)
        finished = self.manager.finished_tasks()[0]
        self.manager.retry(info.task_id)
        self.assertEqual((before.status, returned.status, added[0].status), ("等待中",) * 3)
        self.assertEqual((finished.status, finished.error, finished.downloaded), ("失败", "synthetic", 5))
        finished.save_path = "consumer-only"
        returned.status = "consumer-only"
        self.assertEqual(self.manager.all_tasks()[0].status, "等待中")
        self.assertNotEqual(info.save_path, "consumer-only")

    def test_query_waits_until_related_fields_are_committed(self):
        entered, release, read_started, read_done = self.event(), self.event(), self.event(), self.event()
        class PausingTask(DownloadTaskInfo):
            pause = False
            def __setattr__(self, name, value):
                super().__setattr__(name, value)
                if name == "total" and self.pause:
                    entered.set()
                    if not release.wait(3):
                        raise AssertionError("未释放字段提交屏障")
        info = PausingTask(task_id="atomic", total=2, downloaded=1)
        self.manager._tasks[info.task_id] = info
        info.pause = True
        values = []
        writer = self.start(lambda: self.manager._update_task(info, total=20, downloaded=10))
        self.assertTrue(entered.wait(3))
        def read():
            read_started.set()
            values.extend(self.manager.all_tasks())
            read_done.set()
        reader = self.start(read)
        self.assertTrue(read_started.wait(3))
        self.assertFalse(read_done.wait(0.05), "查询看到了事务只写一半的状态")
        release.set()
        self.join(writer)
        self.join(reader)
        self.assertEqual((values[0].total, values[0].downloaded), (20, 10))

    def test_collection_query_uses_one_lock_for_all_entries(self):
        entered, release, write_started, write_done = self.event(), self.event(), self.event(), self.event()
        class PausingSnapshot(DownloadTaskInfo):
            def snapshot(self):
                entered.set()
                if not release.wait(3):
                    raise AssertionError("未释放集合快照屏障")
                return super().snapshot()
        first = PausingSnapshot(task_id="first", total=1)
        second = DownloadTaskInfo(task_id="second", total=1)
        self.manager._tasks.update(first=first, second=second)
        values = []
        reader = self.start(lambda: values.extend(self.manager.all_tasks()))
        self.assertTrue(entered.wait(3))
        def write():
            write_started.set()
            self.manager._update_task(second, total=2)
            write_done.set()
        writer = self.start(write)
        self.assertTrue(write_started.wait(3))
        self.assertFalse(write_done.wait(0.05))
        release.set()
        self.join(reader)
        self.join(writer)
        self.assertEqual([item.total for item in values], [1, 1])
        self.assertEqual([item.total for item in self.manager.all_tasks()], [1, 2])

    def test_explicit_retry_snapshots_resolve_current_task_and_deduplicate(self):
        _, info = self.task("失败", convert_to_gif=True, gif_path=str(self.root / "result.gif"),
                            cookies="synthetic=fixture", audio_url="https://example.invalid/audio")
        stale = self.manager.finished_tasks()[0]
        absent = DownloadTaskInfo(task_id="absent", status="失败")
        self.manager._update_task(info, status="下载中")
        self.assertEqual(self.manager.retry_tasks([stale, absent]), 0)
        self.manager._update_task(info, status="失败", error="old", downloaded=123, speed=12)
        self.activate(info)
        self.assertEqual(self.manager.retry_tasks([stale, stale]), 0)
        with self.manager._lock:
            self.manager._cancel_flags.pop(info.task_id)
            self.manager._active = 0
        destination = self.root / "new"
        count = self.manager.retry_tasks([stale, stale, absent], rebase_to=str(destination))
        self.assertEqual(count, 1)
        self.assertEqual(self.manager._queue, [info.task_id])
        self.assertEqual((info.status, info.error, info.downloaded, info.speed), ("等待中", "", 0, 0))
        self.assertEqual(Path(info.save_path).parent, destination)
        self.assertEqual(Path(info.gif_path).parent, destination)
        self.assertEqual((stale.status, stale.save_path), ("失败", str(self.root / "video.mp4")))
        self.assertEqual(self.manager._cookies[info.task_id], "synthetic=fixture")
        self.assertEqual(self.manager._plans[info.task_id][0][1], "https://example.invalid/audio")
        self.assertEqual(self.manager.retry_tasks([stale]), 0)

    def test_bulk_cancel_checks_current_state_and_ignores_duplicate_or_missing_ids(self):
        returned, info = self.task()
        missing = DownloadTaskInfo(task_id="absent", status="等待中")
        stale = returned.snapshot()
        stale.status = "失败"
        self.assertEqual(self.manager.cancel_tasks([stale, stale, missing]), 1)
        self.assertEqual(info.status, "已取消")
        self.assertEqual(self.manager.cancel_tasks([returned]), 0)
        self.manager.retry(info.task_id)
        self.manager._update_task(info, status="合并中")
        self.activate(info)
        self.assertEqual(self.manager.cancel_tasks([returned]), 1)
        self.assertTrue(self.manager._cancel_flags[info.task_id].is_set())

    def test_cancel_slot_can_query_and_retry_without_deadlock_or_reversing_queue(self):
        returned, info = self.task()
        receiver = Receiver()
        def retry(snapshot):
            self.manager.all_tasks()
            if snapshot.status == "已取消":
                self.manager.retry(snapshot.task_id)
        self.manager.task_progress.connect(retry, Qt.ConnectionType.DirectConnection)
        self.manager.task_progress.connect(receiver.record, Qt.ConnectionType.QueuedConnection)
        worker = self.start(lambda: self.manager.cancel(returned.task_id))
        self.join(worker)
        self.assertEqual(info.status, "等待中")
        self.app.processEvents()
        self.assertEqual([item.status for item in receiver.values], ["已取消", "等待中"])

    def test_slow_direct_slot_holds_no_manager_lock_and_preserves_queued_order(self):
        _, info = self.task()
        entered, release, committed = self.event(), self.event(), self.event()
        receiver = Receiver()
        def pause(snapshot):
            if snapshot.downloaded == 1:
                entered.set()
                if not release.wait(3):
                    raise AssertionError("未释放通知屏障")
        self.manager.task_progress.connect(pause, Qt.ConnectionType.DirectConnection)
        self.manager.task_progress.connect(receiver.record, Qt.ConnectionType.QueuedConnection)
        first = self.start(lambda: self.manager._update_task(info, signal=self.manager.task_progress, downloaded=1))
        self.assertTrue(entered.wait(3))
        def second_update():
            self.manager._update_task(info, signal=self.manager.task_progress, downloaded=2)
            committed.set()
        second = self.start(second_update)
        self.assertTrue(committed.wait(3), "Qt槽执行期间仍占着管理器锁")
        self.assertEqual(self.manager.all_tasks()[0].downloaded, 2)
        release.set()
        self.join(first)
        self.join(second)
        self.app.processEvents()
        self.assertEqual([item.downloaded for item in receiver.values], [1, 2])

    def test_parallel_writers_keep_query_and_queued_snapshots_coherent(self):
        _, info = self.task()
        receiver = Receiver()
        self.manager.task_progress.connect(receiver.record, Qt.ConnectionType.QueuedConnection)
        barrier = threading.Barrier(3)
        def write(base):
            barrier.wait(3)
            for number in range(base, base + 150):
                self.manager._update_task(info, signal=self.manager.task_progress,
                                          total=number * 2, downloaded=number, speed=float(number))
        first, second = self.start(lambda: write(1)), self.start(lambda: write(1001))
        barrier.wait(3)
        for _ in range(250):
            item = self.manager.all_tasks()[0]
            self.assertEqual(item.total, item.downloaded * 2)
            self.assertEqual(item.speed, float(item.downloaded))
        self.join(first)
        self.join(second)
        self.assertEqual(receiver.values, [])
        self.app.processEvents()
        self.assertEqual(len(receiver.values), 300)
        self.assertEqual(len({id(item) for item in receiver.values}), 300)
        for item in receiver.values:
            self.assertEqual(item.total, item.downloaded * 2)
            self.assertEqual(item.speed, float(item.downloaded))

    def test_terminal_then_direct_retry_keeps_delayed_finished_snapshot(self):
        _, info = self.task()
        self.activate(info)
        receiver = Receiver()
        self.manager.task_finished.connect(lambda snapshot: self.manager.retry(snapshot.task_id),
                                           Qt.ConnectionType.DirectConnection)
        self.manager.task_finished.connect(receiver.record, Qt.ConnectionType.QueuedConnection)
        self.manager.task_progress.connect(receiver.record, Qt.ConnectionType.QueuedConnection)
        with patch.object(self.manager, "_download_once", side_effect=MissingDependency("synthetic")), patch("app.core.downloader.log.error"):
            job = self.start(lambda: self.manager._run(info.task_id))
            self.join(job)
        self.assertEqual(info.status, "等待中")
        self.app.processEvents()
        self.assertEqual([item.status for item in receiver.values], ["失败", "等待中"])
        self.assertEqual([item.error for item in receiver.values], ["synthetic", ""])

    def test_terminal_task_cannot_be_removed_or_retried_while_worker_finishes(self):
        _, info = self.task()
        self.activate(info)
        entered, release = self.event(), self.event()
        original_finish = self.manager._finish_task
        def finish(current):
            entered.set()
            if not release.wait(3):
                raise AssertionError("未释放任务收尾屏障")
            original_finish(current)
        def download(current, *args):
            self.manager._update_task(current, status="已完成", total=5, downloaded=5)
        with patch.object(self.manager, "_finish_task", side_effect=finish), patch.object(self.manager, "_download_once", side_effect=download):
            job = self.start(lambda: self.manager._run(info.task_id))
            self.assertTrue(entered.wait(3))
            self.assertEqual(self.manager.finished_tasks(), [])
            self.assertEqual(self.manager.remove_tasks([info.task_id]), 0)
            self.assertEqual(self.manager.clear_finished(), 0)
            self.assertEqual(self.manager.retry_tasks(self.manager.all_tasks()), 0)
            self.manager.retry(info.task_id)
            self.assertEqual(info.status, "已完成")
            release.set()
            self.join(job)
        finished = self.manager.finished_tasks()
        self.assertEqual(len(finished), 1)
        self.assertEqual(self.manager.remove_tasks([info.task_id]), 1)
        self.assertEqual((finished[0].status, finished[0].downloaded), ("已完成", 5))

    def gif_run(self, cancel=False):
        from app.core.mp4_to_gif import ConversionCancelled
        _, info = self.task(convert_to_gif=True, gif_path=str(self.root / "result.gif"))
        self.activate(info)
        entered, release = self.event(), self.event()
        receiver = Receiver()
        self.manager.task_progress.connect(receiver.record, Qt.ConnectionType.QueuedConnection)
        self.manager.task_finished.connect(receiver.record, Qt.ConnectionType.QueuedConnection)
        def fetch(url, path, *args, **kwargs):
            Path(path).write_bytes(b"video" * 8192)
            return 40960
        def convert(source, destination, *, cancel, **kwargs):
            entered.set()
            if not release.wait(3):
                raise AssertionError("未释放GIF转换屏障")
            if cancel.is_set():
                raise ConversionCancelled("synthetic")
            Path(destination).write_bytes(b"GIF89a")
        with patch.object(self.manager, "_fetch_track", side_effect=fetch), patch("app.core.mp4_to_gif.convert", side_effect=convert):
            job = self.start(lambda: self.manager._run(info.task_id))
            self.assertTrue(entered.wait(3))
            view = self.manager.all_tasks()[0]
            self.assertEqual(view.status, "转换中")
            self.assertTrue(Path(view.save_path).exists())
            self.assertEqual(self.manager.finished_tasks(), [])
            self.assertEqual(self.manager.remove_tasks([info.task_id]), 0)
            self.manager.retry(info.task_id)
            self.assertEqual(info.status, "转换中")
            if cancel:
                self.manager.cancel(info.task_id)
            release.set()
            self.join(job)
        self.app.processEvents()
        return info, receiver.values

    def test_gif_conversion_is_not_a_finished_video_before_atomic_final_path(self):
        info, values = self.gif_run()
        self.assertEqual((info.status, info.total, info.downloaded), ("已完成", 6, 6))
        self.assertEqual(info.save_path, str(self.root / "result.gif"))
        self.assertFalse((self.root / "video.mp4").exists())
        for item in values:
            if item.status == "已完成":
                self.assertEqual((item.save_path, item.total, item.downloaded), (info.save_path, 6, 6))

    def test_cancel_during_gif_conversion_preserves_source_video(self):
        info, values = self.gif_run(cancel=True)
        self.assertEqual(info.status, "已取消")
        self.assertTrue((self.root / "video.mp4").exists())
        self.assertFalse((self.root / "result.gif").exists())
        self.assertNotIn("已完成", [item.status for item in values])

    def test_jm_progress_snapshots_keep_page_counts_and_finished_cookie_contract(self):
        _, info = self.task(cookies="synthetic=fixture")
        self.manager._update_task(info, url="jmcomic://album/123", save_path=str(self.root / "album"))
        self.activate(info)
        receiver = Receiver()
        self.manager.task_progress.connect(receiver.record, Qt.ConnectionType.QueuedConnection)
        self.manager.task_finished.connect(receiver.record, Qt.ConnectionType.QueuedConnection)
        entered, release = self.event(), self.event()
        def download(url, path, cookie, *, cancel, progress_cb):
            self.assertEqual(cookie, "synthetic=fixture")
            progress_cb(1, 2)
            entered.set()
            if not release.wait(3):
                raise AssertionError("未释放漫画进度屏障")
            progress_cb(2, 2)
        with patch("app.core.jmcomic_bridge.download_jmcomic", side_effect=download):
            job = self.start(lambda: self.manager._run(info.task_id))
            self.assertTrue(entered.wait(3))
            first = self.manager.all_tasks()[0]
            self.assertEqual((first.downloaded, first.total, first.status), (1, 2, "下载中"))
            release.set()
            self.join(job)
        self.app.processEvents()
        self.assertEqual((first.downloaded, first.total), (1, 2))
        self.assertEqual([(item.downloaded, item.total) for item in receiver.values],
                         [(0, 0), (1, 2), (2, 2), (2, 2), (2, 2)])
        self.assertEqual(receiver.values[-1].status, "已完成")
        self.assertEqual(self.manager._cookies[info.task_id], "synthetic=fixture")

    def test_mutable_signal_value_is_shared_by_slots_but_not_manager(self):
        _, info = self.task()
        seen = []
        def mutate(snapshot):
            snapshot.name = "consumer-local"
        self.manager.task_progress.connect(mutate, Qt.ConnectionType.DirectConnection)
        self.manager.task_progress.connect(lambda snapshot: seen.append(snapshot.name),
                                           Qt.ConnectionType.DirectConnection)
        self.manager._update_task(info, signal=self.manager.task_progress, downloaded=1)
        self.assertEqual(seen, ["consumer-local"])
        self.assertEqual(self.manager.all_tasks()[0].name, "测试视频")


if __name__ == "__main__":
    unittest.main()
