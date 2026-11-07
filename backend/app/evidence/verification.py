"""Replay and verify persisted field evidence."""

import math
from datetime import datetime

from ..common.clock import SHANGHAI, date_from_text
from ..contracts import PreparedEvent
from .core import (
    BASIS_RE,
    CYCLE_ACTIONS,
    EXERCISES,
    INTENSITIES,
    MEAL_SLOTS,
    NUTRIENTS,
    POLICY_VERSION,
    GroundingError,
    _bare_number,
    _durations,
    _explicit_time,
    _nutrition,
    _quantities,
    _single,
    _weights,
)

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
    if rule in ('context-hour-v1', 'context-minute-v1', 'context-kg-v1'):
        # 追问答案裸数字：单位由问句上下文提供（规则名编码换算），重放 = 数字 × 换算
        value = _bare_number(quote)
        if value is None:
            return None, None
        if rule == 'context-hour-v1':
            return value * 60, 'min'
        return value, ('min' if rule == 'context-minute-v1' else 'kg')
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
