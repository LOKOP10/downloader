# -*- coding: utf-8 -*-
"""任务通知的顺序发布。

状态事务与信号发送之间只有这一个组件：事务在锁内把「信号 + 快照」交给队列，
发送由单一分派者按入队顺序完成，且分派期间不持任何管理器锁。

这样做的原因：Qt 的直接连接槽可能在 emit 里回调管理器（例如完成槽立刻重试），
若发送时还持锁就会死锁；若允许多个分派者并发发送，同一接收方又会看到乱序。
"""
import threading
from collections import deque


class TaskEventBus:
    """按入队顺序发送任务快照，任意时刻只有一个分派者。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._queue = deque()
        self._emitting = False

    def enqueue(self, signal, snapshot):
        """追加一条待发通知；调用方需持有任务事务锁以保证事务内顺序。"""
        self._queue.append((signal, snapshot))

    def enqueue_many(self, notifications):
        """一次事务里产生多条通知时按顺序追加。"""
        self._queue.extend(notifications)

    def drain(self):
        """发送队列里的全部通知；重入调用直接返回，新事件排在当前发送之后。"""
        with self._lock:
            if self._emitting:
                return
            self._emitting = True
        while True:
            with self._lock:
                if not self._queue:
                    self._emitting = False
                    return
                signal, snapshot = self._queue.popleft()
            try:
                signal.emit(snapshot)
            except BaseException:
                with self._lock:
                    self._emitting = False
                raise
