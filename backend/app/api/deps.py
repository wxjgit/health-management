from fastapi import HTTPException, Request
from functools import wraps
from inspect import signature


def get_engine(request: Request):
    engine = request.app.state.engine
    with engine.lock:
        pending = engine.repo.conn.execute("SELECT 1 FROM deletion_jobs WHERE state='business_pending' LIMIT 1").fetchone()
        if pending and request.url.path != '/api/deletions' and not request.url.path.startswith('/api/deletions/'):
            raise HTTPException(503, '相关隐私清理尚未完成，请重试删除任务。')
    return engine


def protected_read(operation):
    """Recheck fences under the same lock as the whole read, not just DI."""
    parameters = signature(operation)

    @wraps(operation)
    def guarded(*args, **kwargs):
        engine = parameters.bind_partial(*args, **kwargs).arguments['engine']
        with engine.lock:
            if engine.repo.conn.execute("SELECT 1 FROM deletion_jobs WHERE state='business_pending' LIMIT 1").fetchone():
                raise HTTPException(503, '相关隐私清理尚未完成，请重试删除任务。')
            return operation(*args, **kwargs)
    return guarded
