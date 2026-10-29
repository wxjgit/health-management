"""Physical SQLite isolation with a bounded, reference-counted engine cache."""
from collections import OrderedDict
from contextlib import contextmanager
from copy import copy
import logging
import shutil
from threading import Condition, Event, Thread
import time
from uuid import UUID

from ..common.execution_budget import RuntimeLimiter
from ..common.errors import RuntimeLimitError
from ..common.usage import UsageController
from ..observability.service import Observability
from .data_owner import DataOwner


def tenant_path(root, user_id):
    # Only internal canonical UUIDs may select a directory, never client paths.
    if not isinstance(user_id, str) or str(UUID(user_id)) != user_id:
        raise ValueError('invalid_internal_user_id')
    parent = root / 'users'
    path = parent / user_id
    if parent.is_symlink() or path.is_symlink() or path.resolve().parent != parent.resolve():
        raise ValueError('invalid_user_data_directory')
    return path


class TenantHost:
    def __init__(self, settings):
        self.settings = settings
        self.owner = DataOwner(settings.data_dir)
        self.guard = Condition()
        self.entries = OrderedDict()
        self.closed = False
        self.purging = set()
        try:
            if (settings.data_dir / '.restore-in-progress').exists():
                raise RuntimeError('incomplete_restore')
            self.observability = Observability(settings)
            self.runtime = RuntimeLimiter(settings)
            self.usage = UsageController(settings)
            self.jobs = TenantJobs(self)
        except BaseException:
            self.close()
            raise

    @contextmanager
    def lease(self, user_id):
        from ..workflows.engine import Engine
        path = tenant_path(self.settings.data_dir, user_id)
        with self.guard:
            if self.closed or user_id in self.purging:
                raise RuntimeLimitError()
            accounts = getattr(self, 'accounts', None)
            if accounts:
                with accounts.connect() as conn:
                    active = conn.execute('SELECT 1 FROM users WHERE id=? AND id NOT IN (SELECT user_id FROM account_deletions)', (user_id,)).fetchone()
                if not active:
                    raise RuntimeLimitError()
            entry = self.entries.get(user_id)
            if entry is None:
                if len(self.entries) >= self.settings.tenant_cache_size:
                    idle = next((key for key, value in self.entries.items() if value[1] == 0), None)
                    if idle is None:
                        raise RuntimeLimitError()
                    self.entries.pop(idle)[0].close()
                settings = copy(self.settings)
                # Internal child path; retain the validated hardened policy.
                settings.data_dir = path
                engine = Engine(settings, services=self, user_id=user_id)
                entry = self.entries[user_id] = [engine, 0]
            self.entries.move_to_end(user_id)
            entry[1] += 1
        try:
            yield entry[0]
        finally:
            with self.guard:
                entry[1] -= 1
                self.guard.notify_all()

    def purge(self, user_id):
        path = tenant_path(self.settings.data_dir, user_id)
        with self.guard:
            self.purging.add(user_id)
            entry = self.entries.get(user_id)
            if entry and entry[1]:
                return False
            if entry:
                self.entries.pop(user_id)[0].close()
            if path.exists():
                shutil.rmtree(path)
            with self.usage.connect() as conn:
                conn.execute('DELETE FROM counters WHERE principal=?', ('user:' + user_id,))
            self.accounts.finish_deletion(user_id)
        return True

    def close(self):
        if self.closed:
            return
        if hasattr(self, 'jobs'):
            self.jobs.close()
        with self.guard:
            self.closed = True
            self.guard.wait_for(lambda: all(entry[1] == 0 for entry in self.entries.values()))
            for engine, _ in self.entries.values():
                engine.close()
            self.entries.clear()
        if hasattr(self, 'runtime'):
            self.runtime.close()
        if hasattr(self, 'observability'):
            self.observability.close()
        self.owner.close()


class TenantJobs:
    """One shared worker visits existing tenants fairly, including evicted ones."""
    def __init__(self, host):
        self.host = host
        self.stop = Event()
        self.thread = None
        self.cursor = ''

    def visit(self, user_id):
        if getattr(self.host, 'accounts', None):
            self.host.accounts.require_privacy(user_id)
        with self.host.usage.scope('user:' + user_id), self.host.lease(user_id) as engine:
            if self.host.settings.scheduler_enabled:
                with engine.lock:
                    last = engine.repo.get_meta('scheduler_last_planned_at', 0)
                if int(time.time() * 1000) - last >= 60000:
                    engine.jobs.plan()
            if self.host.settings.job_runner_enabled:
                engine.jobs.consume()
                engine.jobs.run_one()

    def start(self):
        def work():
            while not self.stop.is_set():
                # The account registry is authoritative; directories alone do not
                # confer an identity or create an account.
                try:
                    with self.host.accounts.connect() as conn:
                        pending = [row['user_id'] for row in conn.execute('SELECT user_id FROM account_deletions')]
                    for uid in pending:
                        try:
                            self.host.purge(uid)
                        except Exception:
                            logging.getLogger(__name__).warning('账号清理尚未完成，将重试')
                    if not (self.host.settings.scheduler_enabled or self.host.settings.job_runner_enabled):
                        self.stop.wait(self.host.settings.job_poll_seconds)
                        continue
                    with self.host.accounts.connect() as conn:
                        users = [row['id'] for row in conn.execute('SELECT id FROM users WHERE id NOT IN (SELECT user_id FROM account_deletions) ORDER BY id')]
                    users = [uid for uid in users if tenant_path(self.host.settings.data_dir, uid).is_dir()]
                    ordered = [uid for uid in users if uid > self.cursor] + [uid for uid in users if uid <= self.cursor]
                    for uid in ordered[:10]:
                        if self.stop.is_set():
                            break
                        self.cursor = uid
                        try:
                            self.visit(uid)
                        except Exception:
                            logging.getLogger(__name__).warning('用户后台任务暂未完成，将在后续轮次重试')
                except Exception:
                    logging.getLogger(__name__).warning('用户任务扫描暂未完成，将重试')
                self.stop.wait(self.host.settings.job_poll_seconds)

        self.thread = Thread(target=work, name='tenant-job-runner', daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join()
