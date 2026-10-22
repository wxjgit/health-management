"""Local, replayable binding between health fields and immutable user text."""
from __future__ import annotations

import copy
import hashlib
import math
import re
from datetime import datetime

from .clock import SHANGHAI, date_from_text
from .schemas import EvidenceSource, EventPatch, ExtractionEvent, FieldEvidence, PreparedEvent

POLICY_VERSION = 'health-grounding-v2'
NUMBER = r'(?:\d+(?:\.\d+)?|一|二|两|三|四|五|六|七|八|九|十|半)'
NUMBERS = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5, '六': 6,
           '七': 7, '八': 8, '九': 9, '十': 10, '半': .5}
WEIGHT_RE = re.compile(rf'(?:(?:体重|称重)\s*)?({NUMBER})\s*(公斤|千克|kg|KG|斤)')
DURATION_RE = re.compile(rf'({NUMBER})\s*(小时|分钟|分)')
QUANTITY_RE = re.compile(rf'({NUMBER})\s*(?:大|小|中)?\s*(碗|份|个|杯|片|克|g|G|毫升|ml|ML|块)')
DATE_RE = re.compile(r'20\d{2}-\d{2}-\d{2}|前天|昨天|昨晚|今天|今日|今晚|今早')
TIME_RE = re.compile(r'(?<!\d)([01]?\d|2[0-3])(?::([0-5]\d)|点(半|[0-5]?\d分?)?)')
EXERCISES = {'跑步': '跑步', '跑了': '跑步', '步行': '步行', '走路': '步行', '走了': '步行',
             '骑车': '骑车', '骑行': '骑车', '健身': '健身', '游泳': '游泳'}
MEAL_SLOTS = {'早餐': 'breakfast', '早饭': 'breakfast', '早上': 'breakfast',
              '午餐': 'lunch', '午饭': 'lunch', '中午': 'lunch',
              '晚餐': 'dinner', '晚饭': 'dinner', '晚上': 'dinner',
              '夜宵': 'snack', '加餐': 'snack'}
INTENSITIES = {'轻松': 'low', '低强度': 'low', '有点累': 'moderate', '中等': 'moderate',
               '中强度': 'moderate', '很累': 'high', '高强度': 'high'}
CYCLE_ACTIONS = {'月经来了': 'start', '经期开始': 'start', '月经开始': 'start', '开始了': 'start',
                 '经期结束': 'end', '月经结束': 'end', '结束了': 'end', '干净了': 'end'}
DEFAULT_HOURS = {'weight': 7, 'sleep': 23, 'exercise': 19, 'cycle': 12}
SLOT_HOURS = {'breakfast': 8, 'lunch': 12, 'dinner': 18, 'snack': 21}
UNITS = {'克': 'g', 'G': 'g', '毫升': 'ml', 'ML': 'ml'}
NUTRIENTS = {'kcal': 'item_kcal', 'protein_g': 'item_protein_g',
             'carbs_g': 'item_carbs_g', 'fat_g': 'item_fat_g'}
NUTRIENT_RE = {
    'kcal': re.compile(rf'(?:(?:热量|能量)\s*[:：]?\s*({NUMBER})\s*(?:kcal|千卡)|({NUMBER})\s*(?:kcal|千卡))', re.I),
    'protein_g': re.compile(rf'(?:蛋白质|蛋白)\s*[:：]?\s*({NUMBER})\s*(?:g|克)', re.I),
    'carbs_g': re.compile(rf'(?:碳水(?:化合物)?)\s*[:：]?\s*({NUMBER})\s*(?:g|克)', re.I),
    'fat_g': re.compile(rf'脂肪\s*[:：]?\s*({NUMBER})\s*(?:g|克)', re.I),
}
BASIS_RE = re.compile(r'每\s*100\s*(?:g|克)', re.I)
SEPARATOR_RE = re.compile(r'[，,。；;、和与及]')


class GroundingError(ValueError):
    pass


def _number(raw):
    return float(NUMBERS[raw] if raw in NUMBERS else raw)


def _unit(raw):
    return UNITS.get(raw, raw.lower())


def _make_source(text, message_id=None, received_at=None):
    digest = hashlib.sha256(text.encode()).hexdigest()
    return EvidenceSource(message_id=message_id or f'inline:{digest[:24]}', text=text,
                          text_hash=digest, received_at=received_at or date_from_text('今天'))


def _merge_sources(*groups):
    result = {}
    for group in groups:
        for raw in group or []:
            source = EvidenceSource.model_validate(raw)
            if source.message_id in result and result[source.message_id].text_hash != source.text_hash:
                raise GroundingError('同一来源编号对应不同原文')
            result[source.message_id] = source
    return [value.model_dump(mode='json') for value in result.values()]


def _scope(source_text, preferred, predicate=lambda value: True):
    if preferred and source_text.count(preferred) == 1 and predicate(preferred):
        return preferred, source_text.index(preferred)
    return source_text, 0


def _unique_span(text, quote, offset=0):
    matches = list(re.finditer(re.escape(quote), text))
    return (offset + matches[0].start(), offset + matches[0].end()) if len(matches) == 1 else None


def _evidence(field, source, text, quote, *, item_index=None, offset=0, start=None, end=None,
              output_value=None, output_unit=None, rule=None, origin='user_text'):
    span = ((offset + start, offset + end) if start is not None and end is not None
            else _unique_span(text, quote, offset) if quote else None)
    if origin == 'user_text' and span is None:
        return None
    normalization = ({'input_text': quote or 'local-default', 'output_value': output_value,
                      'output_unit': output_unit, 'rule_version': rule} if rule else None)
    return FieldEvidence(
        field=field, item_index=item_index, quote=quote, start=span[0] if span else None,
        end=span[1] if span else None, source=origin, source_message_id=source.message_id,
        source_text_hash=source.text_hash, normalization=normalization,
        policy_version=POLICY_VERSION,
    ).model_dump(mode='json')


def _single(values):
    return values[0] if len(values) == 1 else None


def _weights(text):
    return [{'quote': m.group(0), 'value': _number(m.group(1)) / (2 if m.group(2) == '斤' else 1),
             'start': m.start(), 'end': m.end()} for m in WEIGHT_RE.finditer(text)]


def _durations(text):
    return [{'quote': m.group(0), 'value': _number(m.group(1)) * (60 if m.group(2) == '小时' else 1),
             'start': m.start(), 'end': m.end()} for m in DURATION_RE.finditer(text)]


def _quantities(text):
    return [{'quote': m.group(0), 'value': _number(m.group(1)), 'unit': _unit(m.group(2)),
             'start': m.start(), 'end': m.end()} for m in QUANTITY_RE.finditer(text)]


def _weight_candidates(text):
    return [(m['quote'], m['value']) for m in _weights(text)]


def _duration_candidates(text):
    return [(m['quote'], m['value']) for m in _durations(text)]


def _quantity_candidates(text):
    return [(m['quote'], m['value'], m['unit']) for m in _quantities(text)]


def _replace_day(value, expected):
    current = datetime.fromisoformat(value) if isinstance(value, str) else value
    current = current if current.tzinfo else current.replace(tzinfo=SHANGHAI)
    return current.replace(year=expected.year, month=expected.month, day=expected.day).isoformat()


def _replace_time(value, hour, minute=0):
    current = datetime.fromisoformat(value) if isinstance(value, str) else value
    current = current if current.tzinfo else current.replace(tzinfo=SHANGHAI)
    return current.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat()


def _explicit_time(text):
    match = TIME_RE.search(text)
    if not match:
        return None
    minute = int(match.group(2)) if match.group(2) else 30 if match.group(3) == '半' else 0
    hour = int(match.group(1))
    if any(word in text[max(0, match.start() - 4):match.start()]
           for word in ('下午', '晚上', '今晚', '晚餐', '晚饭')) and hour < 12:
        hour += 12
    return {'quote': match.group(0), 'hour': hour, 'minute': minute,
            'start': match.start(), 'end': match.end()}


def _ground_date(data, source, text, offset):
    if not data.get('occurred_at'):
        return
    matches = list(DATE_RE.finditer(text))
    chosen = matches[0] if len(matches) == 1 else None
    if len(matches) > 1:
        day = datetime.fromisoformat(data['occurred_at']).date()
        same = [m for m in matches if date_from_text(m.group(0), source.received_at).date() == day]
        chosen = same[0] if len(same) == 1 else None
    if chosen:
        expected = date_from_text(chosen.group(0), source.received_at)
        data['occurred_at'] = _replace_day(data['occurred_at'], expected)
        data['evidence'].append(_evidence('occurred_at', source, text, chosen.group(0), offset=offset,
            start=chosen.start(), end=chosen.end(), output_value=expected.date().isoformat(), rule='local-date-v1'))
    elif not matches:
        expected = source.received_at.astimezone(SHANGHAI)
        data['occurred_at'] = _replace_day(data['occurred_at'], expected)
        data['evidence'].append(_evidence('occurred_at', source, text, None,
            output_value=expected.date().isoformat(), rule='default-today-v1', origin='derived'))
    else:
        data['occurred_at'] = None
        return
    explicit = _explicit_time(text)
    if explicit:
        hour, minute = explicit['hour'], explicit['minute']
        data['occurred_at'] = _replace_time(data['occurred_at'], hour, minute)
        data['evidence'].append(_evidence('occurred_at', source, text, explicit['quote'], offset=offset,
            start=explicit['start'], end=explicit['end'], output_value=f'{hour:02d}:{minute:02d}',
            rule='explicit-time-v1'))
    else:
        hour = SLOT_HOURS.get(data.get('meal_slot'), 12) if data['kind'] == 'meal' else DEFAULT_HOURS[data['kind']]
        data['occurred_at'] = _replace_time(data['occurred_at'], hour)
        data['evidence'].append(_evidence('occurred_at', source, text, None,
            output_value=f'{hour:02d}:00', rule=f'default-time-{data["kind"]}-v1', origin='derived'))


def _value_match(kind, text):
    matches = _weights(text) if kind == 'weight' else _durations(text)
    if len(matches) <= 1:
        return _single(matches)
    anchors = ['体重', '称重'] if kind == 'weight' else ['睡', '失眠'] if kind == 'sleep' else [*EXERCISES, '运动']
    positions = [text.index(anchor) for anchor in anchors if anchor in text]
    if not positions:
        return None
    ranked = sorted((min(abs(m['start'] - pos) for pos in positions), index, m)
                    for index, m in enumerate(matches))
    return ranked[0][2] if ranked[0][0] < ranked[1][0] else None


def _ground_value(data, source, text, offset):
    if data['kind'] not in ('weight', 'sleep', 'exercise'):
        return
    selected = _value_match(data['kind'], text)
    if not selected or not math.isfinite(selected['value']):
        data['value'] = None
        return
    data['value'] = selected['value']
    data['evidence'].append(_evidence('value', source, text, selected['quote'], offset=offset,
        start=selected['start'], end=selected['end'], output_value=selected['value'],
        output_unit='kg' if data['kind'] == 'weight' else 'min',
        rule='weight-unit-v1' if data['kind'] == 'weight' else 'duration-minute-v1'))


def _ground_simple_fields(data, source, text, offset):
    if data['kind'] == 'meal':
        found = [(quote, value) for quote, value in MEAL_SLOTS.items() if quote in text]
        values = {value for _, value in found}
        if len(values) == 1:
            value = next(iter(values)); quote = next(q for q, v in found if v == value)
            data['meal_slot'] = value
            data['evidence'].append(_evidence('meal_slot', source, text, quote, offset=offset,
                output_value=value, rule='meal-slot-v1'))
        elif data.get('meal_slot'):
            data['meal_slot'] = None
    if data['kind'] == 'exercise':
        found = [(quote, value) for quote, value in EXERCISES.items() if quote in text]
        values = {value for _, value in found}
        if len(values) == 1:
            value = next(iter(values)); quote = next(q for q, v in found if v == value)
            data['exercise_type'] = value
            data['evidence'].append(_evidence('exercise_type', source, text, quote, offset=offset,
                output_value=value, rule='exercise-alias-v1'))
        elif data.get('exercise_type'):
            data['exercise_type'] = None
        found = [(quote, value) for quote, value in INTENSITIES.items() if quote in text]
        values = {value for _, value in found}
        if len(values) == 1:
            value = next(iter(values)); quote = next(q for q, v in found if v == value)
            data['intensity'] = value
            data['evidence'].append(_evidence('intensity', source, text, quote, offset=offset,
                output_value=value, rule='intensity-alias-v1'))
        else:
            data['intensity'] = 'unknown'
    if data['kind'] == 'sleep' and data.get('quality'):
        value = data['quality']
        if text.count(value) == 1:
            data['evidence'].append(_evidence('quality', source, text, value, offset=offset,
                output_value=value, rule='exact-text-v1'))
        else:
            data['quality'] = None
    if data['kind'] == 'cycle':
        found = [(quote, value) for quote, value in CYCLE_ACTIONS.items() if quote in text]
        values = {value for _, value in found}
        if len(values) == 1:
            value = next(iter(values)); quote = next(q for q, v in found if v == value)
            data['cycle_action'] = value
            data['evidence'].append(_evidence('cycle_action', source, text, quote, offset=offset,
                output_value=value, rule='cycle-action-v1'))
        else:
            data['cycle_action'] = None


def _clause(text, position):
    points = [m.start() for m in SEPARATOR_RE.finditer(text)]
    return (max((p + 1 for p in points if p < position), default=0),
            min((p for p in points if p > position), default=len(text)))


def _name_spans(items, text):
    used, result = set(), {}
    for index, item in enumerate(items):
        matches = list(re.finditer(re.escape(item.get('name') or ''), text)) if item.get('name') else []
        match = next((m for m in matches if (m.start(), m.end()) not in used), None)
        if match:
            used.add((match.start(), match.end())); result[index] = (match.start(), match.end())
    return result


def _bind_quantities(items, text, names):
    quantities, used, result = _quantities(text), set(), {}
    for index in sorted(names, key=lambda key: names[key][0]):
        start, end = names[index]; left, right = _clause(text, start)
        choices = [(i, q) for i, q in enumerate(quantities)
                   if i not in used and left <= q['start'] and q['end'] <= right]
        if len(choices) != 1:
            ranked = sorted((min(abs(q['end'] - start), abs(q['start'] - end)), i, q)
                            for i, q in enumerate(quantities) if i not in used)
            choices = ([(ranked[0][1], ranked[0][2])] if ranked and ranked[0][0] <= 6 and
                       (len(ranked) == 1 or ranked[0][0] < ranked[1][0]) else [])
        if len(choices) == 1:
            q_index, quantity = choices[0]; used.add(q_index); result[index] = quantity
    return result


def _nutrition(text):
    result = {}
    for key, pattern in NUTRIENT_RE.items():
        matches = list(pattern.finditer(text))
        if len(matches) == 1:
            match = matches[0]; raw = next(group for group in match.groups() if group is not None)
            result[key] = {'quote': match.group(0), 'value': _number(raw),
                           'start': match.start(), 'end': match.end()}
    return result


def _ground_items(data, source, text, offset):
    items = data.get('items', []); names = _name_spans(items, text); bindings = _bind_quantities(items, text, names)
    for index, item in enumerate(items):
        if index in names:
            start, end = names[index]; value = item['name']
            data['evidence'].append(_evidence('item_name', source, text, value, item_index=index,
                offset=offset, start=start, end=end, output_value=value, rule='exact-text-v1'))
        else:
            item['name'] = None
        quantity = bindings.get(index)
        if quantity:
            item['quantity'], item['unit'] = quantity['value'], quantity['unit']
            for field, value in [('item_quantity', quantity['value']), ('item_unit', quantity['unit'])]:
                data['evidence'].append(_evidence(field, source, text, quantity['quote'], item_index=index,
                    offset=offset, start=quantity['start'], end=quantity['end'], output_value=value,
                    output_unit=quantity['unit'] if field == 'item_quantity' else None, rule='portion-unit-v1'))
        elif item.get('quantity') is not None:
            item['quantity'], item['unit'] = None, '份'
        for key, field in [('size', 'item_size'), ('cooking', 'item_cooking')]:
            value = item.get(key)
            if value and text.count(value) == 1:
                data['evidence'].append(_evidence(field, source, text, value, item_index=index,
                    offset=offset, output_value=value, rule='exact-text-v1'))
            elif value:
                item[key] = None
        for key in NUTRIENTS:
            item[key] = None
        item['source'] = 'unknown'
        if len(items) == 1:
            local, local_offset = text, offset
        elif index in names:
            left, right = _clause(text, names[index][0]); local, local_offset = text[left:right], offset + left
        else:
            continue
        nutrients = _nutrition(local)
        for key, match in nutrients.items():
            item[key] = match['value']
            data['evidence'].append(_evidence(NUTRIENTS[key], source, local, match['quote'], item_index=index,
                offset=local_offset, start=match['start'], end=match['end'], output_value=match['value'],
                output_unit='kcal' if key == 'kcal' else 'g', rule='nutrition-value-v1'))
        if nutrients:
            item['source'] = 'user'; bases = list(BASIS_RE.finditer(local))
            item['nutrition_basis'] = 'per_100g' if len(bases) == 1 else 'consumed'
            if len(bases) == 1:
                basis = bases[0]
                data['evidence'].append(_evidence('item_nutrition_basis', source, local, basis.group(0),
                    item_index=index, offset=local_offset, start=basis.start(), end=basis.end(),
                    output_value='per_100g', rule='nutrition-basis-v1'))


def _ground_adjacent_nutrition(data, source, text, offset, all_item_names):
    """Bind a trailing nutrition clause to the uniquely nearest food entity."""
    if data['kind'] != 'meal':
        return
    entities = []
    for name in dict.fromkeys(name for name in all_item_names if name):
        entities.extend((match.start(), match.end(), name) for match in re.finditer(re.escape(name), text))
    if not entities:
        return
    for index, item in enumerate(data.get('items', [])):
        if not item.get('name') or any(item.get(key) is not None for key in NUTRIENTS):
            continue
        parsed = _nutrition(text); accepted = {}
        for key, match in parsed.items():
            ranked = sorted((min(abs(match['start'] - end), abs(match['end'] - start)), name)
                            for start, end, name in entities)
            if ranked and ranked[0][1] == item['name'] and (len(ranked) == 1 or ranked[0][0] < ranked[1][0]):
                accepted[key] = match
        for key, match in accepted.items():
            item[key] = match['value']
            data['evidence'].append(_evidence(NUTRIENTS[key], source, text, match['quote'], item_index=index,
                offset=offset, start=match['start'], end=match['end'], output_value=match['value'],
                output_unit='kcal' if key == 'kcal' else 'g', rule='nutrition-value-v1'))
        if accepted:
            item['source'] = 'user'; bases = list(BASIS_RE.finditer(text))
            if len(bases) == 1:
                basis = bases[0]; item['nutrition_basis'] = 'per_100g'
                data['evidence'].append(_evidence('item_nutrition_basis', source, text, basis.group(0),
                    item_index=index, offset=offset, start=basis.start(), end=basis.end(),
                    output_value='per_100g', rule='nutrition-basis-v1'))


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


def ground_patch(patch, answer, event_kind, requested_fields, item_index=None, source_message_id=None):
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


def _existing(field, prior, item_index=None):
    found = [copy.deepcopy(entry) for entry in prior.get('evidence', [])
             if entry.get('field') == field and entry.get('item_index') == item_index]
    return found or [FieldEvidence(field=field, item_index=item_index,
                                   source='unverified_legacy', ambiguity=True).model_dump(mode='json')]


def ground_correction(event, prior, correction_text, source_message_id=None):
    candidate = ExtractionEvent.model_validate(event); before = prior.get('data', prior)
    data = candidate.model_dump(mode='json'); source = _make_source(correction_text, source_message_id)
    data['evidence'] = []; data['evidence_sources'] = _merge_sources(
        before.get('evidence_sources', []), [source.model_dump(mode='json')])
    data['raw_text'] = (before.get('raw_text', '') + '，纠正：' + correction_text)[:4000]
    data['evidence'].extend(_existing('occurred_at', before))
    if before.get('value') != data.get('value'):
        matches = _weights(correction_text) if data['kind'] == 'weight' else _durations(correction_text)
        selected = matches[-1] if len(matches) > 1 and re.search(r'不是|改成|改为|实际|应为', correction_text) else _single(matches)
        if selected:
            data['value'] = selected['value']
            data['evidence'].append(_evidence('value', source, correction_text, selected['quote'],
                start=selected['start'], end=selected['end'], output_value=selected['value'],
                output_unit='kg' if data['kind'] == 'weight' else 'min',
                rule='weight-unit-v1' if data['kind'] == 'weight' else 'duration-minute-v1'))
        else:
            data['value'] = None
    elif data.get('value') is not None:
        data['evidence'].extend(_existing('value', before))
    old_items = before.get('items', [])
    for index, item in enumerate(data.get('items', [])):
        old = old_items[index] if index < len(old_items) else {}
        for key, field in [('name', 'item_name'), ('size', 'item_size'), ('cooking', 'item_cooking')]:
            if item.get(key) != old.get(key):
                value = item.get(key)
                if value and correction_text.count(value) == 1:
                    data['evidence'].append(_evidence(field, source, correction_text, value, item_index=index,
                        output_value=value, rule='exact-text-v1'))
                else:
                    item[key] = None
            elif item.get(key) is not None:
                data['evidence'].extend(_existing(field, before, index))
        if any(item.get(key) != old.get(key) for key in ('quantity', 'unit')):
            matches = _quantities(correction_text)
            selected = matches[-1] if len(matches) > 1 and re.search(r'不是|改成|改为|实际|应为', correction_text) else _single(matches)
            if selected:
                item['quantity'], item['unit'] = selected['value'], selected['unit']
                for field, value in [('item_quantity', selected['value']), ('item_unit', selected['unit'])]:
                    data['evidence'].append(_evidence(field, source, correction_text, selected['quote'],
                        item_index=index, start=selected['start'], end=selected['end'], output_value=value,
                        output_unit=selected['unit'] if field == 'item_quantity' else None, rule='portion-unit-v1'))
            else:
                item['quantity'], item['unit'] = None, '份'
        elif item.get('quantity') is not None:
            data['evidence'].extend(_existing('item_quantity', before, index)); data['evidence'].extend(_existing('item_unit', before, index))
        parsed = _nutrition(correction_text)
        for key, field in NUTRIENTS.items():
            if key in parsed:
                match = parsed.get(key)
                item[key] = match['value']
                data['evidence'].append(_evidence(field, source, correction_text, match['quote'], item_index=index,
                    start=match['start'], end=match['end'], output_value=match['value'],
                    output_unit='kcal' if key == 'kcal' else 'g', rule='nutrition-value-v1'))
            elif old.get(key) is not None:
                item[key] = old.get(key)
                data['evidence'].extend(_existing(field, before, index))
            else:
                item[key] = None
        item['source'] = 'user' if any(item.get(key) is not None for key in NUTRIENTS) else 'unknown'
        bases = list(BASIS_RE.finditer(correction_text))
        old_basis = (old.get('nutrition_reference') or {}).get('basis', old.get('nutrition_basis', 'consumed'))
        if len(bases) == 1:
            basis = bases[0]; item['nutrition_basis'] = 'per_100g'
            data['evidence'].append(_evidence('item_nutrition_basis', source, correction_text, basis.group(0),
                item_index=index, start=basis.start(), end=basis.end(), output_value='per_100g', rule='nutrition-basis-v1'))
        elif old_basis == 'per_100g':
            item['nutrition_basis'] = 'per_100g'
            data['evidence'].extend(_existing('item_nutrition_basis', before, index))
        else:
            item['nutrition_basis'] = 'consumed'
    alias_fields = {
        'meal_slot': (MEAL_SLOTS, 'meal-slot-v1', None),
        'exercise_type': (EXERCISES, 'exercise-alias-v1', None),
        'intensity': (INTENSITIES, 'intensity-alias-v1', 'unknown'),
        'cycle_action': (CYCLE_ACTIONS, 'cycle-action-v1', None),
    }
    for field, (aliases, rule, empty) in alias_fields.items():
        if data.get(field) != before.get(field):
            found = [(quote, value) for quote, value in aliases.items() if quote in correction_text]
            values = {value for _, value in found}
            if len(values) == 1:
                value = next(iter(values)); quote = next(q for q, v in found if v == value)
                data[field] = value
                data['evidence'].append(_evidence(field, source, correction_text, quote,
                    output_value=value, rule=rule))
            else:
                data[field] = empty
        elif data.get(field) not in (None, 'unknown'):
            data['evidence'].extend(_existing(field, before))
    if data.get('quality') != before.get('quality'):
        quality = data.get('quality')
        if quality and correction_text.count(quality) == 1:
            data['evidence'].append(_evidence('quality', source, correction_text, quality,
                output_value=quality, rule='exact-text-v1'))
        else:
            data['quality'] = None
    elif data.get('quality') is not None:
        data['evidence'].extend(_existing('quality', before))
    data['evidence'] = [entry for entry in data['evidence'] if entry]
    return ExtractionEvent.model_validate(data)


def merge_patch_evidence(event, patch):
    data = copy.deepcopy(event); new = [entry.model_dump(mode='json') for entry in patch.evidence]
    data['evidence_sources'] = _merge_sources(data.get('evidence_sources', []),
        [source.model_dump(mode='json') for source in patch.evidence_sources])
    keys = {(entry['field'], entry.get('item_index')) for entry in new}
    data['evidence'] = [entry for entry in data.get('evidence', [])
                        if (entry['field'], entry.get('item_index')) not in keys]
    data['evidence'].extend(new)
    return data


def _equal(left, right):
    return (math.isclose(float(left), float(right), abs_tol=1e-6) if
            isinstance(left, (int, float)) and isinstance(right, (int, float)) else left == right)


def _replay(evidence, source):
    trace = evidence.normalization
    if trace is None or trace.input_text != (evidence.quote or 'local-default'):
        raise GroundingError('证据缺少可重放的归一化记录')
    rule, quote = trace.rule_version, evidence.quote or ''
    if rule == 'weight-unit-v1':
        match = _single(_weights(quote)); return (match['value'], 'kg') if match else (None, None)
    if rule == 'duration-minute-v1':
        match = _single(_durations(quote)); return (match['value'], 'min') if match else (None, None)
    if rule == 'portion-unit-v1':
        match = _single(_quantities(quote))
        return ((match['unit'] if evidence.field == 'item_unit' else match['value']),
                None if evidence.field == 'item_unit' else match['unit']) if match else (None, None)
    if rule == 'nutrition-value-v1':
        key = next((key for key, field in NUTRIENTS.items() if field == evidence.field), None)
        match = _nutrition(quote).get(key) if key else None
        return (match['value'], 'kcal' if key == 'kcal' else 'g') if match else (None, None)
    if rule == 'local-date-v1': return date_from_text(quote, source.received_at).date().isoformat(), None
    if rule == 'default-today-v1': return source.received_at.astimezone(SHANGHAI).date().isoformat(), None
    if rule == 'explicit-time-v1':
        parsed = _explicit_time(quote); return (f'{parsed["hour"]:02d}:{parsed["minute"]:02d}', None) if parsed else (None, None)
    if rule.startswith('default-time-'): return trace.output_value, None
    if rule == 'meal-slot-v1': return MEAL_SLOTS.get(quote), None
    if rule == 'exercise-alias-v1': return EXERCISES.get(quote), None
    if rule == 'intensity-alias-v1': return INTENSITIES.get(quote), None
    if rule == 'cycle-action-v1': return CYCLE_ACTIONS.get(quote), None
    if rule == 'nutrition-basis-v1': return ('per_100g', None) if BASIS_RE.fullmatch(quote) else (None, None)
    if rule == 'exact-text-v1': return quote, None
    raise GroundingError('未知归一化规则')


def _actual(event, evidence):
    field = evidence.field
    if field == 'occurred_at':
        moment = event.occurred_at.astimezone(SHANGHAI)
        if evidence.normalization is None:
            return moment.isoformat()
        return moment.strftime('%H:%M') if 'time-' in evidence.normalization.rule_version else moment.date().isoformat()
    if not field.startswith('item_'):
        return getattr(event, field)
    if evidence.item_index is None or evidence.item_index >= len(event.items):
        raise GroundingError('证据食物索引越界')
    key = {'item_name': 'name', 'item_quantity': 'quantity', 'item_unit': 'unit', 'item_size': 'size',
           'item_cooking': 'cooking', 'item_kcal': 'kcal', 'item_protein_g': 'protein_g',
           'item_carbs_g': 'carbs_g', 'item_fat_g': 'fat_g', 'item_nutrition_basis': 'nutrition_basis'}[field]
    item = event.items[evidence.item_index]
    if field == 'item_nutrition_basis' and item.nutrition_reference is not None:
        return item.nutrition_reference.basis
    return getattr(item, key)


def _verified(event):
    sources = {source.message_id: source for source in event.evidence_sources}; verified = {}
    for evidence in event.evidence:
        if evidence.source not in ('user_text', 'user_confirmed', 'derived'):
            continue
        if evidence.policy_version != POLICY_VERSION or evidence.ambiguity:
            raise GroundingError('证据策略无效或仍有歧义')
        source = sources.get(evidence.source_message_id)
        if not source or source.text_hash != evidence.source_text_hash:
            raise GroundingError('证据缺少不可变原文来源')
        if evidence.quote is not None:
            if evidence.start is None or evidence.end is None or source.text[evidence.start:evidence.end] != evidence.quote:
                raise GroundingError('证据片段与原文范围不一致')
        elif evidence.source == 'user_text':
            raise GroundingError('用户原文证据缺少片段')
        replayed, unit = _replay(evidence, source); trace = evidence.normalization
        if not _equal(replayed, trace.output_value) or unit != trace.output_unit:
            raise GroundingError('归一化记录无法从原文重放')
        actual = _actual(event, evidence)
        if evidence.field in NUTRIENTS.values():
            item = event.items[evidence.item_index]
            basis = item.nutrition_reference.basis if item.nutrition_reference else item.nutrition_basis
            expected = replayed * item.quantity / 100 if basis == 'per_100g' else replayed
            if not _equal(round(expected, 1), actual):
                raise GroundingError('营养证据与当前食用量不一致')
        elif not _equal(replayed, actual):
            raise GroundingError('证据不能证明当前字段值')
        key = (evidence.field, evidence.item_index)
        if evidence.field != 'occurred_at' and key in verified and not _equal(verified[key], actual):
            raise GroundingError('同一字段存在冲突证据')
        verified[key] = actual
    return verified


def _prior(prior, field, index):
    if prior is None: return object()
    if field.startswith('item_'):
        if index is None or index >= len(prior.get('items', [])): return object()
        key = {'item_name': 'name', 'item_quantity': 'quantity', 'item_unit': 'unit', 'item_size': 'size',
               'item_cooking': 'cooking', 'item_kcal': 'kcal', 'item_protein_g': 'protein_g',
               'item_carbs_g': 'carbs_g', 'item_fat_g': 'fat_g', 'item_nutrition_basis': 'nutrition_basis'}[field]
        return prior['items'][index].get(key)
    value = prior.get(field)
    if field == 'occurred_at' and value:
        return datetime.fromisoformat(value).astimezone(SHANGHAI).isoformat()
    return value


def _require(event, verified, field, index=None, prior=None):
    if (field, index) in verified: return
    samples = [entry for entry in event.evidence if entry.field == field and entry.item_index == index]
    legacy = any(entry.source in ('unverified_legacy', 'existing_record') for entry in samples)
    if legacy and _equal(_actual(event, samples[0]), _prior(prior, field, index)): return
    raise GroundingError(f'{field} 缺少能证明当前值的证据')


def assert_grounded(record, prior=None):
    event = PreparedEvent.model_validate(record); verified = _verified(event)
    _require(event, verified, 'occurred_at', prior=prior)
    time_rules = {entry.normalization.rule_version for entry in event.evidence
                  if entry.field == 'occurred_at' and entry.normalization and
                  (entry.field, entry.item_index) in verified}
    if ('occurred_at', None) in verified and not any('time-' in rule for rule in time_rules):
        raise GroundingError('发生时刻缺少可重放规则')
    if event.kind in ('weight', 'sleep', 'exercise'): _require(event, verified, 'value', prior=prior)
    if event.kind == 'cycle': _require(event, verified, 'cycle_action', prior=prior)
    for field, value in [('meal_slot', event.meal_slot), ('exercise_type', event.exercise_type), ('quality', event.quality)]:
        if value is not None: _require(event, verified, field, prior=prior)
    if event.intensity != 'unknown': _require(event, verified, 'intensity', prior=prior)
    if event.kind == 'meal':
        for index, item in enumerate(event.items):
            _require(event, verified, 'item_name', index, prior)
            if item.quantity is not None:
                _require(event, verified, 'item_quantity', index, prior); _require(event, verified, 'item_unit', index, prior)
            if item.size is not None: _require(event, verified, 'item_size', index, prior)
            if item.cooking is not None: _require(event, verified, 'item_cooking', index, prior)
            basis = item.nutrition_reference.basis if item.nutrition_reference else item.nutrition_basis
            if basis == 'per_100g' and 'user' in item.nutrition_sources.values():
                _require(event, verified, 'item_nutrition_basis', index, prior)
            for key, field in NUTRIENTS.items():
                if item.nutrition_sources.get(key) == 'user': _require(event, verified, field, index, prior)
    return event
