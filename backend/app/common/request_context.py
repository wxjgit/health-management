"""Per HTTP attempt correlation, separate from business idempotency keys."""

from contextvars import ContextVar
import logging


request_id = ContextVar('http_request_id', default=None)
logger = logging.getLogger('app.errors')


def log_failure(code, *, correlation_id=None, status=None):
    from ..observability.context import current
    from ..observability.contracts import FALLBACKS, safe_code, status_class, valid_id
    from ..observability.logging import SafeEvent
    import json
    safe = safe_code(code)
    service = current.get()
    if service is not None:
        service.emit('operation_failed', code=safe, status_class=status_class(status))
        if safe in FALLBACKS:
            service.emit('model_fallback', purpose=FALLBACKS[safe], reason='model_error')
    else:
        data = {'event': 'operation_failed', 'code': safe, 'status_class': status_class(status)}
        correlation = correlation_id or request_id.get()
        if valid_id(correlation):
            data['request_id'] = correlation
        logger.warning(SafeEvent(json.dumps(data)))
