"""Versioned derivations; historical artifacts retain their original window."""
from ..common import clock

ALGORITHMS = {
    'sleep_intake': 'sleep-intake-v1', 'weekday_intake': 'weekday-intake-v1',
    'review': 'review-v1', 'plan': 'editable-targets-v2', 'summary': 'monthly-summary-v1',
}

def derived_metadata(kind, data):
    from ..privacy.provenance import DerivedData
    algorithm = data.get('rule_version') or data.get('strategy_version') or ALGORITHMS.get(kind, 'custom-v1')
    window = data.get('window') or ([data['start'], data['end']] if 'start' in data and 'end' in data else None)
    if kind == 'plan' and data.get('schedule'):
        window = [data['schedule'][0]['date'], data['schedule'][-1]['date']]
    result = DerivedData({**data, 'memory': {
        'schema_version': 1, 'algorithm': kind, 'algorithm_version': algorithm,
        'generated_at': clock.stamp(), 'window': window,
        'parameters': ({'window_days': 28, 'minimum_group_samples': 3, 'sleep_split_minutes': 420}
                       if kind == 'sleep_intake' else
                       {'window_days': 28, 'minimum_group_samples': 3} if kind == 'weekday_intake' else
                       {'period': data.get('period'), 'complete_diet_days_only': True} if kind == 'review' else
                       {'confirmation_change_percent': 5, 'schedule_days': 7} if kind == 'plan' else
                       {'aggregation': 'monthly', 'retention_years': 1} if kind == 'summary' else {}),
        'status': 'valid', 'invalid_reason': None, 'recomputable': kind != 'summary',
    }})
    result.provenance = getattr(data, 'provenance', None)
    return result

def invalidate_data(data, reason, *, plan=False):
    memory = data.get('memory') or {'schema_version': 1, 'algorithm_version': None}
    return {**data, 'memory': {**memory, 'status': 'needs_review' if plan else 'stale', 'invalid_reason': reason}}

def derived_view(data, *, rolling=False, reference=None):
    memory = data.get('memory')
    if memory is None:
        return {**data, 'memory': {'schema_version': 1, 'status': 'unversioned',
                                 'algorithm_version': None, 'invalid_reason': 'legacy_version_unknown'}}
    expected = ALGORITHMS.get(memory.get('algorithm'))
    if expected and memory.get('algorithm_version') != expected:
        return invalidate_data(data, 'algorithm_changed', plan=memory.get('algorithm') == 'plan')
    if rolling and memory.get('window') and memory['window'][-1] != (reference or clock.now().date()).isoformat():
        return invalidate_data(data, 'window_changed')
    if memory.get('algorithm') == 'plan' and memory.get('window') and memory['window'][-1] < (reference or clock.now().date()).isoformat():
        return invalidate_data(data, 'plan_window_ended', plan=True)
    return data
