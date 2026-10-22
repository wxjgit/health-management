"""Prepare serving-specific nutrition before either persistence path writes a meal."""
import copy
import hashlib
import json
import logging
import re

from .schemas import NutritionValues, NutritionEstimates, PreparedEvent

logger = logging.getLogger(__name__)
# Serving tuples: kcal, protein, carbs, fat. Only used in clearly labelled demo mode.
DEMO_FIXTURES = {
    '牛肉面': (550, 24, 70, 19), '米饭': (230, 4, 50, 1.5),
    '鸡胸肉': (165, 31, 0, 3.6), '鸡蛋': (75, 6.5, .5, 5), '卤蛋': (80, 6.5, 1, 5.5),
    '牛奶': (150, 8, 12, 8), '酸奶': (150, 7, 20, 4.5), '燕麦': (190, 6, 32, 4),
    '面包': (90, 3, 16, 1.5), '苹果': (95, .5, 25, .3), '香蕉': (105, 1.3, 27, .4),
    '西兰花': (35, 2.8, 7, .4), '沙拉': (180, 5, 15, 11), '炸鸡': (500, 28, 25, 32),
    '饺子': (400, 18, 55, 12), '豆腐': (100, 10, 3, 5), '面条': (300, 9, 58, 3),
    '红薯': (180, 3, 42, .3), '咖啡': (5, .3, .5, 0), '奶茶': (400, 4, 65, 14),
}
KEYS = ['kcal', 'protein_g', 'carbs_g', 'fat_g']
EVIDENCE_FIELDS = {'kcal': 'item_kcal', 'protein_g': 'item_protein_g',
                   'carbs_g': 'item_carbs_g', 'fat_g': 'item_fat_g'}


def estimate(draft, mode):
    if draft['kind'] != 'meal':
        return draft
    for item in draft['items']:
        if item['source'] == 'user':
            item['nutrition_source'] = '用户提供'
        elif mode == 'demo' and item['name'] in DEMO_FIXTURES:
            scale = item['quantity'] if item['quantity'] is not None else 1
            if item['unit'] in ('克', 'g', '毫升', 'ml'):
                scale /= 100 if item['name'] not in ['牛奶'] else 250
            scale *= .75 if item.get('size') == '小' else 1.3 if item.get('size') == '大' else 1
            for key, value in zip(KEYS, DEMO_FIXTURES[item['name']]):
                item[key] = round(value * scale, 1)
            item['nutrition_source'] = '演示固定值（非已核验营养库）'
            item['uncertainty'] = '演示粗估；份量与做法可能造成明显误差'
        elif item['source'] == 'model_estimate':
            item['nutrition_source'] = '模型粗估（未核验）'
        else:
            item['nutrition_source'] = '未找到营养来源'
        if item['kcal'] is not None:
            item['kcal_range'] = [round(item['kcal'] * .75), round(item['kcal'] * 1.25)] if item['source'] != 'user' else [item['kcal'], item['kcal']]
    return draft


def item_fingerprint(item):
    descriptor = {key: item.get(key) for key in ('name', 'quantity', 'unit', 'size', 'cooking')}
    descriptor['unit'] = {'克': 'g', '毫升': 'ml'}.get(descriptor['unit'], descriptor['unit'])
    return hashlib.sha256(json.dumps(descriptor, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


def _reference(item, evidence, item_index):
    """Only locally grounded user values may become an authoritative reference."""
    trusted = {(entry.get('field'), entry.get('item_index')) for entry in evidence
               if entry.get('source') in ('user_text', 'user_confirmed')
               and not entry.get('ambiguity') and entry.get('policy_version') == 'health-grounding-v2'}
    stored = (item.get('nutrition_reference') or {}).get('values', {})
    values = {key: (stored[key] if key in stored else item[key]) for key in KEYS
              if (key in stored or item.get(key) is not None)
              and (EVIDENCE_FIELDS[key], item_index) in trusted}
    basis = (item.get('nutrition_reference') or {}).get('basis', item.get('nutrition_basis', 'consumed'))
    if basis == 'per_100g' and ('item_nutrition_basis', item_index) not in trusted:
        return None
    if values:
        return {'basis': basis, 'values': values, 'fingerprint': item_fingerprint(item)}
    return None


def _stamp(item, reason=None):
    sources = item['nutrition_sources']
    complete = all(item.get(key) is not None for key in KEYS)
    if not sources:
        item['nutrition_status'] = 'unavailable'
        item['nutrition_source'] = '营养未知'
    elif not complete:
        item['nutrition_status'] = 'partial'
        origin = ('模型粗估（未核验）' if 'model_estimate' in sources.values()
                  else '演示固定值' if 'demo_fixture' in sources.values() else '用户提供')
        item['nutrition_source'] = '部分营养已知：' + origin
    elif any(v in ('model_estimate', 'demo_fixture') for v in sources.values()):
        item['nutrition_status'] = 'estimated'
        item['nutrition_source'] = ('演示固定值（非已核验营养库）' if 'demo_fixture' in sources.values()
                                    else '模型粗估（未核验）')
    else:
        item['nutrition_status'] = 'user_provided'
        item['nutrition_source'] = '用户提供'
    if 'user' in sources.values() and any(v != 'user' for v in sources.values()):
        item['nutrition_source'] = '用户提供 + ' + ('演示固定值' if 'demo_fixture' in sources.values() else '模型粗估（未核验）')
    item['source'] = 'model_estimate' if 'model_estimate' in sources.values() else 'user' if 'user' in sources.values() else 'unknown'
    item['nutrition_reason'] = reason
    item['kcal_range'] = None
    if item.get('kcal') is not None:
        if sources.get('kcal') == 'user':
            item['kcal_range'] = [item['kcal'], item['kcal']]
        else:
            # An illustrative error band, not a statistical confidence interval.
            item['kcal_range'] = [round(item['kcal'] * .75, 1), round(item['kcal'] * 1.25, 1)]
            item['uncertainty'] = (item['uncertainty'] + '；热量范围为±25%示意误差带，不是统计置信区间')[:200]


def prepare_nutrition(draft, mode, estimator, previous=None, user_text='', reuse_unavailable=False):
    """Keep authoritative values; estimate only missing/invalidated serving-specific fields."""
    event = copy.deepcopy(draft)
    if event['kind'] != 'meal':
        return event
    previous_items = (previous or {}).get('items', [])
    evidence = event.get('evidence', [])
    previous_evidence = (previous or {}).get('evidence', [])
    candidates = []
    claimed = set()
    supplied_now = bool(re.search(r'热量|kcal|千卡|蛋白质|碳水|脂肪|每\s*100', user_text, re.I))
    for index, item in enumerate(event['items']):
        fingerprint = item_fingerprint(item)
        # Prefer a matching descriptor so changing/reordering one food preserves the others.
        old_index = next((i for i, old in enumerate(previous_items)
                          if i not in claimed and item_fingerprint(old) == fingerprint), None)
        if old_index is None and index < len(previous_items) and index not in claimed:
            old_index = index
        old = previous_items[old_index] if old_index is not None else {}
        if old_index is not None:
            claimed.add(old_index)
        reference = _reference(item, evidence, index)
        old_reference = _reference(old, previous_evidence, old_index) if old else None
        if old_reference and (not reference or not supplied_now):
            reference = old_reference
        known_reference = {k: v for k, v in (reference or {}).get('values', {}).items() if v is not None}
        known_old_reference = {k: v for k, v in (old_reference or {}).get('values', {}).items() if v is not None}
        same_reference = known_reference == known_old_reference and (
            (reference or {}).get('basis') == (old_reference or {}).get('basis'))
        if (old.get('nutrition_fingerprint') == fingerprint and same_reference
                and (reuse_unavailable or old.get('nutrition_status') in ('estimated', 'user_provided'))):
            for key in (*KEYS, 'source', 'nutrition_basis', 'nutrition_status', 'nutrition_sources',
                        'nutrition_reference', 'nutrition_fingerprint', 'nutrition_source',
                        'nutrition_reason', 'uncertainty', 'kcal_range'):
                if key in old:
                    item[key] = copy.deepcopy(old[key])
            continue
        # Ignore parse-time estimates. Their quantity/basis has not been checked by this step.
        for key in KEYS:
            item[key] = None
        item.update(nutrition_basis='consumed', nutrition_sources={}, nutrition_reference=reference,
                    nutrition_fingerprint=fingerprint, uncertainty='尚未获得营养估算')
        if reference:
            factor = 1
            if reference['basis'] == 'per_100g':
                if (item.get('quantity') is None or item.get('unit') not in ('g', '克')
                        or old and any(item.get(k) != old.get(k) for k in ('name', 'cooking'))):
                    _stamp(item, '每100g营养需要当前食物的实际克数；更换食物或做法后需重新确认标签。')
                    continue
                factor = item['quantity'] / 100
            elif reference['fingerprint'] != fingerprint:
                _stamp(item, '原用户营养对应旧食物或分量，已保留原值；请确认每100g标签或当前实际食用总量。')
                continue
            values = NutritionValues.model_validate(reference['values']).model_dump(exclude_none=True)
            for key, value in values.items():
                item[key] = round(value * factor, 1)
                item['nutrition_sources'][key] = 'user'
            item['uncertainty'] = ('依据用户提供的每100g标签按实际克数换算' if factor != 1 or reference['basis'] == 'per_100g'
                                   else '用户提供的实际食用总量营养信息')
        missing = [key for key in KEYS if item.get(key) is None]
        if not missing:
            _stamp(item)
        elif item.get('quantity') is None:
            _stamp(item, '实际分量未知，暂不估算缺失营养；没有按一份或零计算。')
        else:
            candidates.append({'index': index, **{k: item.get(k) for k in ('name', 'quantity', 'unit', 'size', 'cooking')},
                               'requested_nutrients': missing,
                               'provided_nutrients': {k: item[k] for k in KEYS if item.get(k) is not None}})

    estimates = {}
    failure = None
    if candidates and mode == 'model':
        try:
            response = NutritionEstimates.model_validate(estimator(candidates))
            requested = {i['index'] for i in candidates}
            for result in response.items:
                if result.index not in requested or result.index in estimates:
                    raise ValueError('营养估算食物索引不匹配')
                estimates[result.index] = result
        except (ValueError, TypeError) as error:
            logger.warning('Nutrition preparation failed (%s)', type(error.__cause__ or error).__name__)
            failure = '营养估算暂时失败，食物和分量已保留，可稍后通过纠正记录重试。'
    for candidate in candidates:
        item = event['items'][candidate['index']]
        proposed = {}
        uncertainty = None
        if mode == 'demo' and item['name'] in DEMO_FIXTURES:
            demo = estimate({'kind': 'meal', 'items': [{**item, 'source': 'unknown'}]}, 'demo')['items'][0]
            proposed = {k: demo[k] for k in candidate['requested_nutrients']}
            uncertainty = demo['uncertainty']
        elif candidate['index'] in estimates:
            result = estimates[candidate['index']]
            proposed = {k: getattr(result, k) for k in candidate['requested_nutrients'] if getattr(result, k) is not None}
            uncertainty = result.uncertainty
        merged = {**{k: item.get(k) for k in KEYS}, **proposed}
        if all(merged[k] is not None for k in KEYS):
            macro_energy = merged['protein_g'] * 4 + merged['carbs_g'] * 4 + merged['fat_g'] * 9
            if abs(macro_energy - merged['kcal']) > max(50, merged['kcal'] * .35):
                proposed = {}
                failure_reason = '估算结果的热量与营养素不一致，未采用；已保留用户提供的数值。'
            else:
                failure_reason = None
        else:
            failure_reason = None
        for key, value in proposed.items():
            item[key] = value
            item['nutrition_sources'][key] = 'demo_fixture' if mode == 'demo' else 'model_estimate'
        if uncertainty:
            item['uncertainty'] = uncertainty
        reason = failure_reason or failure
        if any(item.get(k) is None for k in KEYS) and not reason:
            reason = '未能可靠估算全部营养，已知部分保留，未知部分没有按零计算。'
        _stamp(item, reason)
    # Provenance for invalidated user nutrition must not survive a portion/food change.
    keep = set()
    for index, item in enumerate(event['items']):
        for key, source in item.get('nutrition_sources', {}).items():
            if source == 'user':
                keep.add((EVIDENCE_FIELDS[key], index))
        reference = item.get('nutrition_reference') or {}
        if reference.get('basis') == 'per_100g' and 'user' in item.get('nutrition_sources', {}).values():
            keep.add(('item_nutrition_basis', index))
    nutrition_fields = {*EVIDENCE_FIELDS.values(), 'item_nutrition_basis'}
    event['evidence'] = [entry for entry in event.get('evidence', [])
                         if entry.get('field') not in nutrition_fields
                         or (entry.get('field'), entry.get('item_index')) in keep]
    # Validate the final values, ranges and provenance before allowing persistence.
    return PreparedEvent.model_validate(event).model_dump(mode='json')


def nutrition_summary(records):
    items = [i for event in records if event['kind'] == 'meal' for i in event['data']['items']]
    if not items:
        return ''
    totals = meal_totals(items)
    if totals['kcal'] is None:
        return '营养说明：当前热量未知。' + '；'.join(dict.fromkeys(
            i.get('nutrition_reason') or '尚未获得营养数据' for i in items))
    partial = any(i.get('kcal') is None for i in items)
    estimated = any(i.get('nutrition_sources', {}).get('kcal') in ('model_estimate', 'demo_fixture')
                    or not i.get('nutrition_sources') and i.get('source') == 'model_estimate'
                    for i in items if i.get('kcal') is not None)
    label = '已知食物热量合计' if partial else '已记录餐食热量合计'
    text = f'{label}{"约" if estimated else " "}{totals["kcal"]:g} kcal'
    text += '（含粗估值，非精确测量）。' if estimated else '（用户提供）。'
    if not totals['complete_nutrition']:
        text += '部分营养未知，不代表零。'
    return text


def meal_totals(items):
    totals = {}
    for key in KEYS:
        known = [i[key] for i in items if i.get(key) is not None]
        totals[key] = round(sum(known), 1) if known else None
    totals['unknown_items'] = sum(i.get('kcal') is None for i in items)
    totals['complete_nutrition'] = all(all(i.get(key) is not None for key in KEYS) for i in items)
    known_energy = [i for i in items if i.get('kcal') is not None]
    totals['kcal_range'] = [round(sum((i.get('kcal_range') or [i['kcal'], i['kcal']])[n]
                                    for i in known_energy)) for n in (0, 1)] if known_energy else None
    return totals
