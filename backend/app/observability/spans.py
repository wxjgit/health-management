"""Timers deliberately avoid recording exceptions, arguments or return values."""
from contextlib import contextmanager
import time

from .context import emit, workflow_kind


def error_outcome(error):
    from ..common.errors import ConflictError, ExecutionDeadlineExceeded
    from langgraph.errors import GraphInterrupt
    import asyncio
    if isinstance(error, GraphInterrupt):
        return 'interrupted'
    if isinstance(error, ExecutionDeadlineExceeded):
        return 'deadline'
    if isinstance(error, ConflictError):
        return 'conflict'
    if isinstance(error, asyncio.CancelledError):
        return 'cancelled'
    return 'error'


@contextmanager
def node_span(node):
    started, outcome = time.monotonic(), 'ok'
    try:
        yield
    except BaseException as error:
        outcome = error_outcome(error)
        raise
    finally:
        emit('node_finished', kind=workflow_kind.get(), node=node, outcome=outcome,
             duration_seconds=time.monotonic()-started)
