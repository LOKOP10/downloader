"""任务事件总线：顺序、重入与异常恢复。"""
import threading
import unittest

from app.core.task_events import TaskEventBus


class _Signal:
    """记录 emit 顺序的最小信号替身。"""

    def __init__(self, name, log, on_emit=None):
        self.name = name
        self.log = log
        self.on_emit = on_emit

    def emit(self, snapshot):
        self.log.append((self.name, snapshot))
        if self.on_emit is not None:
            self.on_emit(snapshot)


class TaskEventBusTest(unittest.TestCase):
    def setUp(self):
        self.bus = TaskEventBus()
        self.log = []

    def test_drain_sends_in_enqueue_order(self):
        for index in range(5):
            self.bus.enqueue(_Signal("p", self.log), index)
        self.bus.drain()
        self.assertEqual([value for _, value in self.log], [0, 1, 2, 3, 4])

    def test_enqueue_many_keeps_batch_order(self):
        notifications = [(_Signal("a", self.log), "x"), (_Signal("b", self.log), "y")]
        self.bus.enqueue_many(notifications)
        self.bus.drain()
        self.assertEqual(self.log, [("a", "x"), ("b", "y")])

    def test_reentrant_enqueue_is_sent_after_current_event(self):
        depth = {"max": 0, "now": 0}

        def nested(_snapshot):
            depth["now"] += 1
            depth["max"] = max(depth["max"], depth["now"])
            if len(self.log) == 1:
                self.bus.enqueue(_Signal("nested", self.log), "inner")
            depth["now"] -= 1

        self.bus.enqueue(_Signal("outer", self.log, nested), "outer")
        self.bus.drain()
        self.assertEqual([value for _, value in self.log], ["outer", "inner"])
        self.assertEqual(depth["max"], 1, "分派必须是单层，不能在 emit 里递归发送")

    def test_concurrent_drain_does_not_duplicate_events(self):
        started = threading.Event()
        release = threading.Event()

        def slow(_snapshot):
            started.set()
            release.wait(5)

        self.bus.enqueue(_Signal("slow", self.log, slow), 1)
        worker = threading.Thread(target=self.bus.drain)
        worker.start()
        self.assertTrue(started.wait(5), "分派线程没有开始发送")
        self.bus.enqueue(_Signal("late", self.log), 2)
        self.bus.drain()   # 第二个分派者应立即返回，不能并发发送
        release.set()
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual([value for _, value in self.log], [1, 2])

    def test_failing_signal_propagates_and_does_not_block_later_drains(self):
        def boom(_snapshot):
            raise RuntimeError("槽里炸了")

        self.bus.enqueue(_Signal("bad", self.log, boom), "bad")
        with self.assertRaises(RuntimeError):
            self.bus.drain()
        self.bus.enqueue(_Signal("good", self.log), "good")
        self.bus.drain()
        self.assertEqual(self.log[-1], ("good", "good"))

    def test_empty_drain_is_a_no_op(self):
        self.bus.drain()
        self.bus.drain()
        self.assertEqual(self.log, [])


if __name__ == "__main__":
    unittest.main()
