"""定义可观测事件的有限词表，并在记录前校验字段。"""
import math
from uuid import UUID

PURPOSES = frozenset({'safety_classification', 'processing_plan', 'correct_selected',
    'patch_draft', 'personalized_advice', 'contextual_reply', 'estimate_nutrition'})
NODES = frozenset({'safety_gate', 'classify_safety', 'safety_response', 'authorize_operation',
    'parse_initial', 'patch_pending', 'normalize_extraction', 'validate_drafts',
    'prepare_nutrition', 'prepare_operations', 'save_ready', 'prepare_clarification',
    'clarify', 'execute_operations', 'route_response', 'query_response', 'fact_response',
    'record_response', 'advice_response', 'compose_response', 'prepare_plan', 'approve_plan',
    'activate_plan', 'analyze', 'review'})
KINDS = frozenset({'chat', 'plan', 'analysis'})
JOBS = frozenset({'archive', 'daily', 'weekly', 'next_meal', 'missing_meal', 'checkin', 'auto_plan', 'outbox'})
STATUS = frozenset({'none', '1xx', '2xx', '3xx', '4xx', '5xx'})
OUTCOMES = frozenset({'ok', 'error', 'interrupted', 'cancelled', 'conflict', 'deadline',
    'replayed', 'timeout', 'network_error', 'proxy_error', 'http_error', 'response_invalid',
    'response_too_large', 'transport_failed', 'envelope_invalid', 'schema_invalid',
    'disconnected', 'incomplete', 'failed', 'skipped', 'done', 'lease_lost', 'retry'})
FALLBACKS = {'model_nutrition_fallback': 'estimate_nutrition',
    'model_reply_fallback': 'contextual_reply', 'model_advice_fallback': 'personalized_advice',
    'model_classifier_fallback': 'safety_classification'}
ENUMS = {'purpose': PURPOSES, 'node': NODES, 'kind': KINDS, 'job_kind': JOBS,
    'status_class': STATUS, 'outcome': OUTCOMES,
    'method': frozenset({'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS', 'other'}),
    'operation': frozenset({'run', 'resume'}),
    'database': frozenset({'business', 'checkpoint', 'job_heartbeat'}),
    'role': frozenset({'engine', 'repository', 'workflow', 'conversation'}),
    'category': frozenset({'busy', 'locked'}),
    'reason': frozenset({'model_error', 'invalid_event', 'queue_full', 'io_error', 'oversize', 'shutdown'}),
    'sink': frozenset({'log', 'metric'}),
    'stage': frozenset({'none', 'connect', 'read', 'write', 'pool', 'overall'})}
EVENTS = {
    'operation_failed': {'code', 'status_class'},
    'http_finished': {'route', 'method', 'status_class', 'outcome', 'duration_seconds'},
    'workflow_finished': {'kind', 'operation', 'outcome', 'duration_seconds'},
    'node_finished': {'kind', 'node', 'outcome', 'duration_seconds'},
    'model_transport_finished': {'purpose', 'status_class', 'outcome', 'stage', 'duration_seconds'},
    'model_contract_finished': {'purpose', 'outcome', 'duration_seconds'},
    'model_fallback': {'purpose', 'reason'},
    'job_finished': {'job_kind', 'outcome', 'duration_seconds'},
}


def status_class(status):
    return f'{status // 100}xx' if type(status) is int and 100 <= status < 600 else 'none'


def safe_code(code):
    from ..common.errors import ERRORS
    extra = set(FALLBACKS) | {'not_found', 'invalid_target', 'request_mismatch',
        'scope_changed', 'preview_required', 'unknown_error', 'safety_policy_blocked',
        'model_tool_window_invalid', 'model_plan_repair'}
    extra |= {
        'model_plan_fact_intent',
        'model_plan_source_text',
        'model_plan_write_in_read',
        'model_plan_view_intent',
        'model_plan_history_refused',
        'model_plan_window',
        'model_plan_meal_scope',
        'model_plan_view_scope',
        'model_plan_fact_source',
        'model_plan_query_kind',
        'model_plan_query_search',
        'model_plan_query_window',
        'model_plan_duplicate_actions',
        'model_plan_correction_kind',
    }
    if isinstance(code, str) and code in ERRORS and code.startswith('safety_'):
        return 'safety_policy_blocked'
    if isinstance(code, str) and (code in ERRORS or code in extra or code in {f'http_{n}' for n in range(400, 600)}):
        return code
    return 'unknown_error'


def valid_id(value):
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def valid_fields(fields, routes):
    for key, value in fields.items():
        if key == 'duration_seconds':
            if type(value) not in (float, int) or not math.isfinite(value) or not 0 <= value <= 1e9:
                return False
        elif key == 'route':
            if not isinstance(value, str) or value not in routes:
                return False
        elif key == 'code':
            if safe_code(value) != value:
                return False
        elif key not in ENUMS or not isinstance(value, str) or value not in ENUMS[key]:
            return False
    return True
