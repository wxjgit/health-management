from fastapi import Request
from .auth import require_user
from functools import wraps
from inspect import signature
from ..common.errors import PrivacyCleanupPending, RuntimeLimitError


def get_engine(request: Request):
    host = request.app.state.engine
    with host.lease(require_user(request)['id']) as engine:
        check_engine(request, engine)
        yield engine


def check_engine(request, engine):
    if not engine.lock.acquire(timeout=1):
        raise RuntimeLimitError()
    try:
        pending = engine.repo.conn.execute("SELECT 1 FROM deletion_jobs WHERE state='business_pending' LIMIT 1").fetchone()
        if pending and request.url.path != '/api/deletions' and not request.url.path.startswith('/api/deletions/'):
            raise PrivacyCleanupPending()
    finally:
        engine.lock.release()


def protected_read(operation):
    """Recheck fences under the same lock as the whole read, not just DI."""
    parameters = signature(operation)

    @wraps(operation)
    def guarded(*args, **kwargs):
        engine = parameters.bind_partial(*args, **kwargs).arguments['engine']
        with engine.runtime.execution('read'), engine.lock:
            if engine.repo.conn.execute("SELECT 1 FROM deletion_jobs WHERE state='business_pending' LIMIT 1").fetchone():
                raise PrivacyCleanupPending()
            return operation(*args, **kwargs)
    return guarded


def bounded_api(operation, lane='read'):
    parameters = signature(operation)

    @wraps(operation)
    def bounded(*args, **kwargs):
        engine = parameters.bind_partial(*args, **kwargs).arguments['engine']
        with engine.runtime.execution(lane):
            return operation(*args, **kwargs)
    return bounded


def managed_api(operation):
    return bounded_api(operation, 'management')
