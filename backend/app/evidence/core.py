"""Local, replayable binding between health fields and immutable user text."""
from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime

from ..common.clock import SHANGHAI, date_from_text
from ..contracts import EvidenceSource, EventPatch, ExtractionEvent, FieldEvidence, PreparedEvent

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
