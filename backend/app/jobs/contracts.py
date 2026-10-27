"""Only local approved task kinds and non-health routing metadata are accepted."""
from datetime import date

FIELDS = {
    'archive': {'period_end'},
    'daily': {'period_end', 'send_notice'},
    'weekly': {'period_end'},
    'next_meal': {'period_end', 'slot', 'clock'},
    'missing_meal': {'period_end', 'slot', 'clock'},
    'checkin': {'period_end', 'clock'},
    'auto_plan': {'period_end', 'schedule_anchor', 'source_job_id', 'source_workflow', 'base_plan_id'},
}


def validate_payload(kind, payload):
    if kind not in FIELDS or not isinstance(payload, dict) or set(payload) - FIELDS[kind]:
        raise ValueError('job_payload_unsupported')
    if 'period_end' not in payload:
        raise ValueError('job_payload_unsupported')
    date.fromisoformat(payload['period_end'])
    if kind == 'auto_plan':
        if set(payload) != FIELDS[kind]:
            raise ValueError('job_payload_unsupported')
        date.fromisoformat(payload['schedule_anchor'])
        for name in ('source_job_id', 'source_workflow', 'base_plan_id'):
            from uuid import UUID
            UUID(payload[name])
    if kind in ('next_meal', 'missing_meal', 'checkin'):
        if set(payload) != FIELDS[kind]:
            raise ValueError('job_payload_unsupported')
        from datetime import time
        time.fromisoformat(payload['clock'])
        if kind != 'checkin' and payload['slot'] not in ('breakfast', 'lunch', 'dinner'):
            raise ValueError('job_payload_unsupported')
    if 'send_notice' in payload and type(payload['send_notice']) is not bool:
        raise ValueError('job_payload_unsupported')
