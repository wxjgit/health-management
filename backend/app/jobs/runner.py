"""One local execution thread, independent heartbeats and bounded admission."""
import logging
import sqlite3
import time
from contextlib import contextmanager
from threading import Event, Lock, Thread
from uuid import uuid4

from ..common.errors import (AppError, DomainValidationError, ExecutionDeadlineExceeded,
                             ModelUnavailableError, RuntimeLimitError, public_error)
from ..common.execution_budget import _budget
from .context import LeaseLost, job_scope
from .handlers import execute_job
from .planner import millis, plan_jobs

logger = logging.getLogger(__name__)


class JobRunner:
    def __init__(self, engine):
        self.engine = engine
        self.stop = Event()
        self.execution = Lock()
        self.threads = []
        self.worker_id = uuid4().hex
        self.last_planned_at = None

    def plan(self, reference=None):
        with self.engine.runtime.execution('management'), self.engine.lock:
            self.engine.deletions.recover()
            plan_jobs(self.engine, reference)
            self.last_planned_at = millis(reference) if reference else int(time.time()*1000)
            self.engine.repo.set_meta('scheduler_last_planned_at', self.last_planned_at)

    @contextmanager
    def heartbeat(self, lease):
        finished = Event()
        deadline = _budget.get().deadline
        settings = self.engine.settings

        def beat():
            from ..infrastructure.budget_connection import BudgetConnection
            from ..infrastructure.repositories.repository import Repository
            from ..common.execution_budget import BudgetLock
            # No schema migration or startup recovery on the heartbeat connection.
            repo = object.__new__(Repository)
            repo.lock = BudgetLock()
            repo.conn = sqlite3.connect(settings.data_dir/'health.db', timeout=1, factory=BudgetConnection)
            repo.conn.row_factory = sqlite3.Row
            repo.conn.database_role = 'job_heartbeat'
            repo.conn.observer = self.engine.observability
            repo.lock.observer = self.engine.observability
            try:
                while not finished.wait(settings.job_heartbeat_seconds):
                    if self.stop.is_set() or time.monotonic() >= deadline:
                        break
                    try:
                        repo.heartbeat_job(lease, settings.job_lease_seconds)
                    except LeaseLost:
                        break
                    except sqlite3.OperationalError:
                        continue
            finally:
                repo.close()

        thread = Thread(target=beat, name='job-heartbeat', daemon=True)
        thread.start()
        try:
            yield
        finally:
            finished.set()
            thread.join(timeout=3)

    def run_one(self, reference=None):
        from ..observability.context import bind, emit, job_attempt
        from ..common.request_context import request_id
        token = request_id.set(None)
        try:
            with bind(self.engine.observability, operation=True):
                started = time.monotonic()
                try:
                    return self._run_one(reference)
                finally:
                    attempt = job_attempt.get()
                    if attempt['kind']:
                        emit('job_finished', job_kind=attempt['kind'], outcome=attempt['outcome'],
                             duration_seconds=time.monotonic()-attempt.get('started', started))
        finally:
            request_id.reset(token)

    def _run_one(self, reference=None):
        if self.stop.is_set() or not self.execution.acquire(False):
            return False
        lease = None
        try:
            try:
                with self.engine.runtime.execution():
                    with self.engine.lock:
                        if self.engine.repo.get_meta('jobs_paused', False):
                            return False
                        lease = self.engine.repo.claim_job(self.worker_id, self.engine.settings.job_lease_seconds,
                            due_at=millis(reference) if reference else None)
                    if not lease:
                        return False
                    with self.heartbeat(lease), job_scope(self.engine.repo, lease, self.stop):
                        execute_job(self.engine, lease, reference)
                return True
            except Exception as error:
                if lease:
                    self.record_failure(lease, error)
                elif not isinstance(error, RuntimeLimitError):
                    logger.warning('任务领取失败，将重试')
                return bool(lease)
        finally:
            self.execution.release()

    def record_failure(self, lease, error):
        repo = self.engine.repo
        if isinstance(error, LeaseLost):
            from ..observability.context import mark_job
            mark_job(outcome='lease_lost')
            return
        category, code, retry, status = 'permanent', 'job_execution_failed', False, 'failed'
        if isinstance(error, ExecutionDeadlineExceeded):
            category, code, retry = 'runtime_timeout', 'execution_deadline_exceeded', True
        elif isinstance(error, (ModelUnavailableError, sqlite3.OperationalError)):
            category, code, retry = 'transient', 'job_dependency_unavailable', True
        elif isinstance(error, DomainValidationError):
            category, code, status = 'business_blocked', public_error(error)[0], 'skipped'
        elif isinstance(error, AppError):
            category, code, retry = 'transient', public_error(error)[0], True
        elif isinstance(error, ValueError):
            code = 'job_payload_unsupported'
        try:
            # New operation: work may have exhausted its deadline already.
            with self.engine.runtime.execution('management'), self.engine.lock:
                repo.finish_scheduled_job(lease, status, category=category, code=code, retry=retry)
        except (LeaseLost, RuntimeLimitError, sqlite3.OperationalError):
            pass
        logger.warning('后台任务未完成 category=%s code=%s', category, code)

    def consume(self):
        from ..observability.context import bind
        from ..common.request_context import request_id
        token = request_id.set(None)
        try:
            with bind(self.engine.observability, operation=True):
                return self._consume()
        finally:
            request_id.reset(token)

    def _consume(self):
        from ..observability.context import emit
        with self.engine.runtime.execution('management'), self.engine.lock:
            event = self.engine.repo.claim_outbox(self.engine.settings.job_lease_seconds)
            if not event:
                return False
            started, outcome = time.monotonic(), 'ok'
            try:
                self.engine.repo.consume_outbox(*event)
            except Exception as error:
                outcome = 'error'
                self.engine.repo.fail_outbox(*event, permanent=isinstance(error, ValueError))
            finally:
                emit('job_finished', job_kind='outbox', outcome=outcome, duration_seconds=time.monotonic()-started)
            return True

    def drain(self, reference=None, limit=64):
        for _ in range(limit):
            consumed = self.consume()
            ran = self.run_one(reference)
            if not consumed and not ran:
                break

    def start(self):
        if self.threads:
            return

        def planning():
            while not self.stop.is_set():
                try:
                    self.plan()
                except Exception:
                    logger.warning('任务规划失败，将重试')
                self.stop.wait(60)

        def working():
            while not self.stop.is_set():
                try:
                    self.consume()
                    self.run_one()
                except Exception:
                    logger.warning('后台任务扫描失败，将重试')
                self.stop.wait(self.engine.settings.job_poll_seconds)

        if self.engine.settings.scheduler_enabled:
            self.threads.append(Thread(target=planning, name='job-planner', daemon=True))
        if self.engine.settings.job_runner_enabled:
            self.threads.append(Thread(target=working, name='job-runner', daemon=True))
        for thread in self.threads:
            thread.start()

    def close(self):
        self.stop.set()
        deadline = time.monotonic()+self.engine.settings.job_shutdown_grace_seconds
        for thread in self.threads:
            thread.join(timeout=max(0,deadline-time.monotonic()))
        # Do not close databases under a live graph/model thread. Hardened runtime
        # supervision terminates stuck work; development waits for bounded I/O.
        for thread in self.threads:
            if thread.is_alive():
                thread.join()
        with self.execution:
            pass
