"""Remove shared message snapshots without guessing new evidence."""

import copy
import hashlib
from uuid import uuid4

from ..evidence import assert_grounded


def minimize_event(data, forbidden=None, *, sources_only=False):
    """Keep verified quotes only, rebind offsets/hash, and verify once more.

    A legacy event that cannot be independently verified must be included in
    the preview's shared-source deletion group instead of silently rewritten.
    """
    assert_grounded(data)
    result = copy.deepcopy(data)
    sources = {source['message_id']: source for source in result['evidence_sources']}
    for entry in result['evidence']:
        source_id = entry.get('source_message_id')
        if not forbidden or source_id not in forbidden or not entry.get('quote'):
            continue
        bounds = forbidden[source_id]
        if bounds['hash'] is not None and entry.get('source_text_hash') != bounds['hash']:
            raise ValueError('共享来源原文不一致，不能证明记录独立')
        if any(entry['start'] < end and start < entry['end'] for start, end in bounds['spans']):
            raise ValueError('保留字段与待删除内容共享证据片段')
    replacements = []
    for source_id, source in sources.items():
        evidence = [entry for entry in result['evidence'] if entry.get('source_message_id') == source_id]
        quotes = list(dict.fromkeys(entry['quote'] for entry in evidence if entry.get('quote')))
        text = '\n'.join(quotes) or 'local-default'
        if len(text) > 4000:
            raise ValueError('独立证据片段超过来源长度限制')
        new_id = f'fragment:{uuid4()}'
        digest = hashlib.sha256(text.encode()).hexdigest()
        offsets, offset = {}, 0
        for quote in quotes:
            offsets[quote] = offset
            offset += len(quote) + 1
        for entry in evidence:
            entry['source_message_id'], entry['source_text_hash'] = new_id, digest
            if entry.get('quote'):
                entry['start'] = offsets[entry['quote']]
                entry['end'] = entry['start'] + len(entry['quote'])
            else:
                entry['start'] = entry['end'] = None
        replacements.append({**source, 'message_id': new_id, 'text': text, 'text_hash': digest})
    result['evidence_sources'] = replacements
    if sources_only:
        return assert_grounded(result).model_dump(mode='json')
    result['feedback'] = []  # Free-form comments cannot be split into proven independent spans.
    kind = result['kind']
    if kind == 'meal':
        detail = '、'.join(f"{item['name']} {item.get('quantity') or ''}{item.get('unit', '')}" for item in result['items'])
    elif kind == 'weight':
        detail = f"体重 {result['value']:g} 公斤"
    elif kind in ('sleep', 'exercise'):
        detail = f"{'睡眠' if kind == 'sleep' else result.get('exercise_type') or '运动'} {result['value']:g} 分钟"
    else:
        detail = f"生理周期 {result.get('cycle_action', '')}"
    result['raw_text'] = f"{result['occurred_at'][:10]} {detail}"[:4000]
    for item in result.get('items', []):
        item['uncertainty'] = '来源片段已最小化，保留原有数值'
        item['nutrition_reason'] = None
        item['nutrition_source'] = '保留原有数值'
    return assert_grounded(result).model_dump(mode='json')
