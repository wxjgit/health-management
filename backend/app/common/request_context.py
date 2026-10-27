"""Per HTTP attempt correlation, separate from business idempotency keys."""

from contextvars import ContextVar
import logging


request_id = ContextVar('http_request_id', default=None)
logger = logging.getLogger('app.errors')


def log_failure(code, *, correlation_id=None, status=None):
    # Never stringify exception objects, requests, model data or exception chains.
    logger.warning('operation_failed code=%s request_id=%s status=%s',
                   code, correlation_id or request_id.get(), status)
