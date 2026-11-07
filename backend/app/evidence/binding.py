"""Bind proposed health fields to immutable source text."""

from ..contracts import EventPatch, ExtractionEvent
from .core import (
    CYCLE_ACTIONS,
    DATE_RE,
    INTENSITIES,
    _bare_number,
    _durations,
    _evidence,
    _ground_adjacent_nutrition,
    _ground_date,
    _ground_items,
    _ground_simple_fields,
    _ground_value,
    _make_source,
    _quantities,
    _scope,
    _single,
    _value_match,
    _weights,
)

def ground_event(event, source_text, source_message_id=None, scope_text=None, all_item_names=None):
    candidate = ExtractionEvent.model_validate(event); data = candidate.model_dump(mode='json')
    source = _make_source(source_text, source_message_id)
    data['evidence'], data['evidence_sources'] = [], [source.model_dump(mode='json')]
    preferred = scope_text if scope_text and source_text.count(scope_text) == 1 else data.get('raw_text')
    outer, outer_offset = _scope(source_text, preferred)
    text, inner_offset = _scope(outer, data.get('raw_text'))
    offset = outer_offset + inner_offset
    _ground_simple_fields(data, source, text, offset)
    date_text, date_offset = _scope(text, data.get('raw_text'), lambda value: bool(DATE_RE.search(value)))
    _ground_date(data, source, date_text, offset + date_offset)
    value_text, value_offset = _scope(text, data.get('raw_text'),
        lambda value: bool(_weights(value) if data['kind'] == 'weight' else _durations(value)))
    _ground_value(data, source, value_text, offset + value_offset)
    if data['kind'] == 'meal':
        _ground_items(data, source, text, offset)
        if text != outer:
            _ground_adjacent_nutrition(data, source, outer, outer_offset,
                                       all_item_names or [item.get('name') for item in data['items']])
    data['evidence'] = [entry for entry in data['evidence'] if entry]
    return ExtractionEvent.model_validate(data)


def _context_rule(kind, context):
    """追问问句提供的单位上下文 → 裸数字答案的换算规则（规则名编码换算，供重放）。"""
    context = context or ''
    if kind == 'weight':
        # 斤=0.5kg 需额外换算，不在本规则覆盖内：宁可不落值也不错记
        return 'context-kg-v1' if any(word in context for word in ('公斤', '千克', 'kg', 'KG')) else None
    if kind in ('sleep', 'exercise'):
        if '小时' in context:
            return 'context-hour-v1'
        if '分钟' in context or '分' in context:
            return 'context-minute-v1'
    return None


def ground_patch(patch, answer, event_kind, requested_fields, item_index=None,
                 source_message_id=None, unit_context=''):
    proposed = EventPatch.model_validate(patch)
    data = proposed.model_dump(mode='json', exclude_none=True, exclude_unset=True)
    source = _make_source(answer, source_message_id)
    data['evidence'], data['evidence_sources'] = [], [source.model_dump(mode='json')]
    if 'value' in requested_fields:
        selected = _value_match(event_kind, answer)
        if selected:
            data['value'] = selected['value']
            data['evidence'].append(_evidence('value', source, answer, selected['quote'],
                start=selected['start'], end=selected['end'], output_value=selected['value'],
                output_unit='kg' if event_kind == 'weight' else 'min',
                rule='weight-unit-v1' if event_kind == 'weight' else 'duration-minute-v1'))
        else:
            # 裸数字答案（"6"）：答案文本无单位，单位来自追问问句上下文；
            # 仅当整串即一个数且问句单位明确时落值，避免猜测
            rule = _context_rule(event_kind, unit_context)
            bare = _bare_number(answer) if rule else None
            if bare is not None:
                value = bare * 60 if rule == 'context-hour-v1' else bare
                data['value'] = value
                data['evidence'].append(_evidence('value', source, answer, answer.strip(),
                    output_value=value, output_unit='kg' if event_kind == 'weight' else 'min',
                    rule=rule))
            else:
                data.pop('value', None)
    if 'items' in requested_fields:
        shell = {'items': data.get('items', []), 'evidence': []}; _ground_items(shell, source, answer, 0)
        if item_index is not None:
            for entry in shell['evidence']:
                if entry.get('item_index') is not None:
                    entry['item_index'] = item_index
        data['items'], data['evidence'] = shell['items'], [*data['evidence'], *shell['evidence']]
    if 'quantity' in requested_fields:
        selected = _single(_quantities(answer))
        if selected:
            actual = item_index if item_index is not None else 0
            data.update(quantity=selected['value'], unit=selected['unit'])
            for field, value in [('item_quantity', selected['value']), ('item_unit', selected['unit'])]:
                data['evidence'].append(_evidence(field, source, answer, selected['quote'], item_index=actual,
                    start=selected['start'], end=selected['end'], output_value=value,
                    output_unit=selected['unit'] if field == 'item_quantity' else None, rule='portion-unit-v1'))
            size = '小' if '小' in selected['quote'] else '大' if '大' in selected['quote'] else None
            if size:
                data['size'] = size
                data['evidence'].append(_evidence('item_size', source, answer, size, item_index=actual,
                    output_value=size, rule='exact-text-v1'))
        else:
            for key in ('quantity', 'unit', 'size'):
                data.pop(key, None)
    if 'occurred_at' in requested_fields:
        shell = {'kind': event_kind, 'occurred_at': data.get('occurred_at') or source.received_at.isoformat(),
                 'meal_slot': None, 'evidence': []}
        _ground_date(shell, source, answer, 0)
        if shell.get('occurred_at'):
            data.update(occurred_at=shell['occurred_at'])
            data['evidence'].extend(shell['evidence'])
        else:
            data.pop('occurred_at', None)
    if 'intensity' in requested_fields:
        found = [(quote, value) for quote, value in INTENSITIES.items() if quote in answer]
        values = {value for _, value in found}
        if len(values) == 1:
            value = next(iter(values)); quote = next(q for q, v in found if v == value)
            data['intensity'] = value
            data['evidence'].append(_evidence('intensity', source, answer, quote,
                output_value=value, rule='intensity-alias-v1'))
        else:
            data.pop('intensity', None)
    if 'cycle_action' in requested_fields:
        found = [(quote, value) for quote, value in CYCLE_ACTIONS.items() if quote in answer]
        values = {value for _, value in found}
        if len(values) == 1:
            value = next(iter(values)); quote = next(q for q, v in found if v == value)
            data['cycle_action'] = value
            data['evidence'].append(_evidence('cycle_action', source, answer, quote,
                output_value=value, rule='cycle-action-v1'))
        else:
            data.pop('cycle_action', None)
    for requested, field in [('quality', 'quality'), ('cooking', 'item_cooking'), ('size', 'item_size')]:
        if requested in requested_fields and data.get(requested):
            value = data[requested]
            if answer.count(value) == 1:
                data['evidence'].append(_evidence(field, source, answer, value,
                    item_index=(item_index if field.startswith('item_') else None),
                    output_value=value, rule='exact-text-v1'))
            else:
                data.pop(requested, None)
    return EventPatch.model_validate(data)
