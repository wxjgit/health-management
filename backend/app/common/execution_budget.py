"""Admission and cooperative deadlines follow actual work, never HTTP lifetimes."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
import json
import os
from pathlib import Path
from threading import BoundedSemaphore, Event, Lock, RLock, Thread, local
import time
from uuid import uuid4

from .errors import ExecutionDeadlineExceeded, RuntimeLimitError

_budget = ContextVar('execution_budget', default=None)


@dataclass
class ExecutionBudget:
    deadline: float

    def remaining(self):
        value = self.deadline - time.monotonic()
        if value <= 0:
            raise ExecutionDeadlineExceeded()
        return value


def check_budget():
    budget = _budget.get()
    if budget is not None:
        budget.remaining()


def remaining_seconds(default):
    budget = _budget.get()
    return min(default, budget.remaining()) if budget else default


class BudgetLock:
    """Keep the existing reentrant lock semantics with bounded waiting."""
    def __init__(self, role='repository', observer=None):
        self._lock = RLock()
        self.role, self.observer, self._depth = role, observer, local()

    def acquire(self, blocking=True, timeout=-1):
        check_budget()
        # Most repository acquisitions are uncontended or reentrant. They do
        # not wait: avoid thousands of histogram updates for zero-wait reads.
        acquired = self._lock.acquire(False)
        if acquired or not blocking:
            if acquired:
                self._depth.value = getattr(self._depth, 'value', 0) + 1
            return acquired
        started = time.monotonic()
        budget = _budget.get()
        if budget:
            timeout = min(timeout, budget.remaining()) if timeout >= 0 else budget.remaining()
        acquired = self._lock.acquire(timeout=timeout)
        self._record(acquired, started)
        if not acquired and budget:
            raise ExecutionDeadlineExceeded()
        return acquired

    def _record(self, acquired, started):
        depth = getattr(self._depth, 'value', 0)
        if not depth:
            from ..observability.context import current
            observer = self.observer or current.get()
            if observer:
                observer.observe('lock_wait_duration_seconds', time.monotonic()-started, role=self.role)
        if acquired:
            self._depth.value = depth + 1

    def release(self):
        self._lock.release()
        self._depth.value = max(0, getattr(self._depth, 'value', 0) - 1)

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()


class RuntimeLimiter:
    def __init__(self, settings, status_path=None):
        self.settings = settings
        self.slots = BoundedSemaphore(settings.runtime_max_executions)
        self.management = BoundedSemaphore(1)
        self.reads = BoundedSemaphore(8)
        self.guard = Lock()
        self.active = {}
        self.stop = Event()
        self.thread = None
        self.status_path = Path(status_path or '/tmp/runtime-status.json')

    @contextmanager
    def execution(self, lane='work'):
        # Nested work shares both slot and deadline, including scheduler calls.
        if _budget.get() is not None:
            check_budget()
            yield
            return
        semaphore = {'management': self.management, 'read': self.reads}.get(lane, self.slots)
        if not semaphore.acquire(False):
            raise RuntimeLimitError()
        key = uuid4().hex
        budget = ExecutionBudget(time.monotonic() + self.settings.execution_seconds)
        with self.guard:
            self.active[key] = budget.deadline
        token = _budget.set(budget)
        try:
            yield
            check_budget()
        finally:
            _budget.reset(token)
            with self.guard:
                self.active.pop(key, None)
            semaphore.release()

    def snapshot(self):
        with self.guard:
            return {'heartbeat': time.monotonic(), 'deadlines': list(self.active.values()), 'ready': True}

    def start(self):
        if self.settings.runtime_profile != 'hardened':
            return
        if os.getuid() == 0:
            raise RuntimeError('invalid_runtime_identity')
        self.thread = Thread(target=self.watch, name='runtime-watchdog', daemon=True)
        self.thread.start()

    def watch(self):
        target = self.status_path
        while not self.stop.is_set():
            snapshot = self.snapshot()
            if any(snapshot['heartbeat'] > d + self.settings.execution_grace_seconds for d in snapshot['deadlines']):
                os._exit(70)
            try:
                temporary = target.with_suffix('.tmp')
                temporary.write_text(json.dumps(snapshot), encoding='utf-8')
                temporary.replace(target)
            except OSError:
                os._exit(71)
            self.stop.wait(1)

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)


def runtime_operation(lane='work'):
    def decorate(operation):
        @wraps(operation)
        def wrapped(self, *args, **kwargs):
            runtime = getattr(self, 'runtime', None) or self.engine.runtime
            from ..observability.context import bind
            observer = getattr(self, 'observability', None) or getattr(getattr(self, 'engine', None), 'observability', None)
            with bind(observer), runtime.execution(lane):
                return operation(self, *args, **kwargs)
        return wrapped
    return decorate
