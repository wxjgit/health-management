"""Ground corrections and merge their field evidence."""

import copy
import re

from ..contracts import ExtractionEvent, FieldEvidence
from .core import (
    BASIS_RE,
    CYCLE_ACTIONS,
    EXERCISES,
    INTENSITIES,
    MEAL_SLOTS,
    NUTRIENTS,
    _durations,
    _evidence,
    _make_source,
    _merge_sources,
    _nutrition,
    _quantities,
    _single,
    _weights,
)

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
