"""Attempt ownership follows LangGraph's propagated execution context."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from ..common.errors import ConflictError

_job = ContextVar('scheduled_job_lease', default=None)
_checking = ContextVar('checking_job_lease', default=False)


class LeaseLost(ConflictError):
    default_code = 'job_lease_lost'


@dataclass(frozen=True)
class JobLease:
    id: str
    generation: int
    token: str
    attempt: int


def check_job(connection=None):
    current = _job.get()
    if current is None or _checking.get():
        return
    token = _checking.set(True)
    try:
        repo, lease, stop = current
        if stop is not None and stop.is_set():
            raise LeaseLost()
        if connection is repo.conn and not connection.in_transaction:
            import sqlite3
            sqlite3.Connection.execute(connection, 'BEGIN IMMEDIATE')
        repo.assert_lease(lease)
    finally:
        _checking.reset(token)


@contextmanager
def job_scope(repo, lease, stop=None):
    token = _job.set((repo, lease, stop))
    try:
        check_job()
        yield
    finally:
        _job.reset(token)
