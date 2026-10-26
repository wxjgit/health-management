"""Collect actual repository reads independently of presentation evidence."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps


_captures = ContextVar('provenance_captures', default=())
_suppressed = ContextVar('provenance_suppressed', default=False)


@dataclass
class ReadSet:
    repo: object
    target: str | None = None
    sources: set = field(default_factory=set)
    scopes: dict = field(default_factory=dict)


class DerivedData(dict):
    """Read metadata travels to its save operation, never into JSON payloads."""

    provenance = None


@contextmanager
def capture(repo, target=None):
    reads = repo.node_reads(target) if target else ReadSet(repo)
    reads.target = target
    token = _captures.set((*_captures.get(), reads))
    suppression = _suppressed.set(False)
    try:
        yield reads
    finally:
        _captures.reset(token)
        _suppressed.reset(suppression)


@contextmanager
def untracked():
    token = _suppressed.set(True)
    try:
        yield
    finally:
        _suppressed.reset(token)


def collectors(repo):
    if _suppressed.get():
        return ()
    return tuple(item for item in _captures.get() if item.repo is repo)


def current_reads(repo):
    active = collectors(repo)
    return active[-1] if active else None


def tracked(operation):
    @wraps(operation)
    def wrapped(repo, *args, **kwargs):
        with capture(repo) as reads:
            result = operation(repo, *args, **kwargs)
            if isinstance(result, dict):
                result = DerivedData(result)
                result.provenance = reads
            return result
    return wrapped
