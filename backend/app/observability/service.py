from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
import re

from . import context
from .contracts import EVENTS, valid_fields, valid_id
from .logging import FileSink, SafeEvent, acquire_logging_boundary, release_logging_boundary
from .metrics import Metrics


class Observability:
    def __init__(self, settings):
        self.enabled = settings.obs_enabled
        self.level = settings.obs_log_level
        self.metrics = Metrics()
        self.routes = {'unmatched'}
        self.key = settings.obs_thread_link_key
        self.key_version = settings.obs_thread_link_key_version
        self.closed = False
        acquire_logging_boundary()
        self.sink = FileSink(settings.data_dir / 'observability', self.drop, self.depth) if self.enabled else None

    def link(self, thread):
        if self.key and isinstance(thread, str):
            return hmac.new(self.key, b'observability-thread\0' + thread.encode(), hashlib.sha256).hexdigest()
        return None

    def drop(self, reason):
        try:
            self.metrics.observe('observability_dropped', 1, {'sink': 'log', 'reason': reason}, self.routes)
        except Exception:
            pass

    def depth(self, value):
        try:
            self.metrics.observe('observability_queue_depth', value, {}, self.routes)
        except Exception:
            pass

    def observe(self, name, value=1, **labels):
        if not self.enabled:
            return
        try:
            if not self.metrics.observe(name, value, labels, self.routes):
                self.metrics.observe('observability_dropped', 1,
                    {'sink': 'metric', 'reason': 'invalid_event'}, self.routes)
        except Exception:
            pass  # Telemetry must not alter business behavior.

    def emit(self, event, **fields):
        if not self.enabled:
            return
        try:
            if event not in EVENTS or set(fields) != EVENTS[event] or not valid_fields(fields, self.routes):
                self.drop('invalid_event')
                return
            from ..common.request_context import request_id
            result = {'schema_version': 1, 'timestamp': datetime.now(timezone.utc).isoformat(),
                      'event': event, **fields}
            for key, value in (('request_id', request_id.get()), ('operation_id', context.operation_id.get())):
                if valid_id(value):
                    result[key] = value
            link = context.thread_link.get()
            if self.key and isinstance(link, str) and re.fullmatch('[a-f0-9]{64}', link):
                result.update(thread_link=link, key_version=self.key_version)
            line = json.dumps(result, ensure_ascii=True, allow_nan=False, separators=(',', ':'))
            if len(line.encode()) > 4096:
                self.drop('oversize')
                return
            self.record_metrics(event, fields)
            # No arbitrary formatting args, exception data or extra dictionary.
            warning = event in {'operation_failed', 'model_fallback'}
            if warning or self.level == 'INFO':
                self.sink.submit(line)
                logging.getLogger('app.telemetry').log(logging.WARNING if warning else logging.INFO, SafeEvent(line))
        except Exception:
            self.drop('invalid_event')

    def record_metrics(self, event, fields):
        mapping = {
            'http_finished': ('http_requests', 'http_request_duration_seconds'),
            'workflow_finished': ('workflow_attempts', None),
            'node_finished': (None, 'workflow_node_duration_seconds'),
            'model_transport_finished': ('model_transport', 'model_transport_duration_seconds'),
            'model_contract_finished': ('model_contract', None),
            'model_fallback': ('model_fallback', None),
            'job_finished': ('job_attempts', 'job_execution_duration_seconds'),
        }
        from .metrics import SPECS
        for name in mapping.get(event, ()):
            if name:
                labels = {key: fields[key] for key in SPECS[name][1]}
                self.observe(name, fields.get('duration_seconds', 1) if name.endswith('_seconds') else 1, **labels)

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.sink:
            self.sink.close()
        release_logging_boundary()
