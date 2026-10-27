from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4
import time

current = ContextVar('observability_service', default=None)
operation_id = ContextVar('observability_operation', default=None)
thread_link = ContextVar('observability_thread', default=None)
processing_mode = ContextVar('observability_mode', default=None)
workflow_kind = ContextVar('observability_workflow_kind', default='chat')
job_attempt = ContextVar('observability_job_attempt', default=None)


@contextmanager
def bind(service, *, operation=False, thread=None, mode=None, kind=None):
    tokens = [(current, current.set(service))]
    if operation:
        tokens.append((operation_id, operation_id.set(str(uuid4()))))
        tokens.append((job_attempt, job_attempt.set({'kind': None, 'outcome': 'error'})))
        tokens.append((thread_link, thread_link.set(None)))
    if thread is not None:
        tokens.append((thread_link, thread_link.set(service.link(thread) if service else None)))
    if mode is not None:
        tokens.append((processing_mode, processing_mode.set(mode)))
    if kind is not None:
        tokens.append((workflow_kind, workflow_kind.set(kind)))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


def emit(event, **fields):
    service = current.get()
    if service is not None:
        service.emit(event, **fields)


def observe(name, value=1, **labels):
    service = current.get()
    if service is not None:
        service.observe(name, value, **labels)


def mark_job(*, kind=None, outcome=None):
    attempt = job_attempt.get()
    if attempt is not None:
        if kind is not None:
            attempt['kind'] = kind
            attempt['started'] = time.monotonic()
        if outcome is not None:
            attempt['outcome'] = outcome
