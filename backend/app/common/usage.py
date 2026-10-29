"""Single-instance admission and durable attempt quotas; stores no health text."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, timedelta
import sqlite3
from threading import BoundedSemaphore
import time

from .clock import now
from .errors import RuntimeLimitError

_principal = ContextVar('usage_principal', default=None)
_controller = ContextVar('usage_controller', default=None)


class UsageController:
    def __init__(self, settings):
        self.settings = settings
        self.path = settings.data_dir / 'usage.db'
        self.slots = BoundedSemaphore(settings.model_max_concurrent)
        with self.connect() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS counters (
                kind TEXT NOT NULL, period TEXT NOT NULL, principal TEXT NOT NULL,
                count INTEGER NOT NULL, PRIMARY KEY(kind, period, principal))''')

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=1)
        try:
            with conn:
                yield conn
        except sqlite3.OperationalError:
            raise RuntimeLimitError() from None
        finally:
            conn.close()

    def principal(self):
        value = _principal.get()
        if value is None:
            raise RuntimeLimitError(code='usage_identity_required')
        return value

    @contextmanager
    def scope(self, principal=None):
        identity = _principal.set(principal) if principal is not None else None
        token = _controller.set(self)
        try:
            yield
        finally:
            _controller.reset(token)
            if identity is not None:
                _principal.reset(identity)

    def reserve(self, kind, period, user_limit, global_limit, code):
        principal = self.principal()
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            # Retain a short history so a request crossing midnight cannot erase
            # the next day's counter. All period values sort chronologically.
            cutoff = (date.fromisoformat(period) - timedelta(days=7)).isoformat() if kind == 'model' else f'{int(period) - 2:020d}'
            conn.execute('DELETE FROM counters WHERE kind=? AND period<?', (kind, cutoff))
            for key, limit in ((principal, user_limit), ('*', global_limit)):
                row = conn.execute('SELECT count FROM counters WHERE kind=? AND period=? AND principal=?',
                                   (kind, period, key)).fetchone()
                if row and row[0] >= limit:
                    raise RuntimeLimitError(code=code)
            for key in (principal, '*'):
                conn.execute('''INSERT INTO counters VALUES (?,?,?,1)
                    ON CONFLICT(kind,period,principal) DO UPDATE SET count=count+1''',
                             (kind, period, key))

    def request(self):
        self.reserve('request', f'{int(time.time()) // 60:020d}', self.settings.user_requests_per_minute,
                     self.settings.global_requests_per_minute, 'request_rate_limited')

    def snapshot(self):
        day = now().date().isoformat()
        with self.connect() as conn:
            row = conn.execute("SELECT count FROM counters WHERE kind='model' AND period=? AND principal=?",
                               (day, self.principal())).fetchone()
        used = row[0] if row else 0
        return {'day': day, 'timezone': 'Asia/Shanghai', 'model_attempts': used,
                'daily_limit': self.settings.user_model_calls_per_day,
                'remaining': max(0, self.settings.user_model_calls_per_day - used)}

    @contextmanager
    def model_attempt(self):
        if not self.slots.acquire(False):
            raise RuntimeLimitError(code='model_concurrency_limited')
        try:
            self.reserve('model', now().date().isoformat(), self.settings.user_model_calls_per_day,
                         self.settings.global_model_calls_per_day, 'daily_model_quota_exceeded')
            yield
        finally:
            self.slots.release()


@contextmanager
def model_admission():
    controller = _controller.get()
    if controller is None:
        # Standalone parser/evaluation calls do not run in an application Engine.
        yield
    else:
        with controller.model_attempt():
            yield
