"""Pure, date-scoped fact resolution shared by advice, safety and planning."""
from .facts import FACT_POLICY_VERSION, is_restriction, normalize_fact


def same_subject(left, right):
    return bool(left.get('subject_key') and left['subject_key'] == right.get('subject_key')
                and left.get('subject_kind') == right.get('subject_kind'))


def overlaps(left, right):
    return max(left.get('valid_from') or '0001-01-01', right.get('valid_from') or '0001-01-01') <= min(
        left.get('valid_until') or '9999-12-31', right.get('valid_until') or '9999-12-31')


def resolve_records(records, day):
    records = [normalize_fact(r) for r in records]
    applicable, pending = [], []
    for record in records:
        if record['lifecycle'] in ('retracted', 'superseded'):
            continue
        if record.get('valid_from') and record['valid_from'] > day:
            continue
        if record['confirmation'] == 'candidate' or record.get('assertion') != 'affirmed' or record.get('time_scope') == 'temporary_unknown':
            pending.append(record)
            continue
        ended = bool(record.get('valid_until') and record['valid_until'] < day)
        if ended and not is_restriction(record):
            continue
        applicable.append({**record, 'lifecycle': 'needs_review' if ended else 'active'})
    masked = set()
    superseded = set()
    # Future permanent changes follow effective dates, even when entered out of order.
    permanent = [r for r in applicable if r['confirmation'] == 'confirmed' and r['type'] == 'food_preference'
                 and not is_restriction(r) and not r.get('valid_until')]
    for record in permanent:
        newer = [r for r in permanent if r.get('auto_update') and same_subject(record, r)
                 and (r.get('valid_from') or '0001-01-01', r['created_at'], r['id']) >
                     (record.get('valid_from') or '0001-01-01', record['created_at'], record['id'])]
        if newer:
            masked.add(record['id'])
            superseded.add(record['id'])
    for record in applicable:
        # Only an explicitly selected, ordinary preference may temporarily cover another.
        if record['confirmation'] == 'confirmed' and not is_restriction(record):
            masked.update(record.get('overrides', []))
        if record.get('supersedes'):
            masked.add(record['supersedes'])
            superseded.add(record['supersedes'])
    visible = [r for r in applicable if r['id'] not in masked]
    conflicts = []
    conflicted = set()
    for index, record in enumerate(visible):
        peers = [other for other in visible[index + 1:] if same_subject(record, other)
                 and record['type'] == other['type'] and record['value'] != other['value']]
        if peers:
            ids = [record['id'], *[p['id'] for p in peers]]
            conflicted.update(ids)
            conflicts.append({'subject': record['subject'], 'ids': ids})
    effective = [{**r, 'confirmation': 'conflict' if r['id'] in conflicted else 'confirmed'} for r in visible
                 if r['id'] not in conflicted or is_restriction(r)]
    return {'effective': effective, 'constraints': [r for r in effective if is_restriction(r)],
            'preferences': [r for r in effective if not is_restriction(r)],
            'conflicts': conflicts, 'pending': pending, 'masked_ids': sorted(masked),
            'superseded_ids': sorted(superseded),
            'reference_date': day, 'policy_version': FACT_POLICY_VERSION}


def fact_projection(record):
    return {**{key: record.get(key) for key in ('type', 'subject', 'value', 'enforcement', 'time_scope',
            'valid_from', 'valid_until', 'source_kind', 'reported_severity', 'lifecycle')},
            'description': (record['content'][:500] if record['type'] == 'legacy_text'
                            else f"{record['subject']}：{record['value']}")}


def require_resolved(view, *, limit=8):
    if view['conflicts']:
        raise ValueError('长期信息存在未解决的冲突，请先在设置中核对。')
    if pending_decisions(view):
        raise ValueError('长期信息的适用期限或含义待核对，请先在设置中补充。')
    if len(view['constraints']) > limit:
        raise ValueError('相关限制超过当前建议处理范围，请先核对限制信息。')


def pending_decisions(view):
    return [r for r in view['pending'] if r.get('assertion') == 'affirmed'
            and (r.get('time_scope') == 'temporary_unknown' or is_restriction(r))]
