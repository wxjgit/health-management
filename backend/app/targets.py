"""Editable starting estimates for general adults, never a deadline-based diet.

PAL bins, 10%/300 kcal deficit cap, BMI scope and 1500–3500 kcal handling are
conservative PRODUCT policies, not individually validated clinical thresholds.
Unsupported cases retain manual, clinician-provided target entry.
"""
import hashlib
import json
import math
import re

from pydantic import ValidationError

from .schemas import Profile, Targets

VERSION = 'mifflin-editable-defaults-v1'
ACTIVITY = {'sedentary': 1.4, 'light': 1.6, 'moderate': 1.8, 'active': 2.0}
SOURCES = [
    {'title': 'Mifflin–St Jeor 静息能量估算原始研究',
     'url': 'https://pubmed.ncbi.nlm.nih.gov/2305711/'},
    {'title': 'NIDDK 活动水平及适用范围', 'url': 'https://www.niddk.nih.gov/bwp'},
    {'title': 'Health Canada 成人宏量营养素参考范围',
     'url': 'https://www.canada.ca/en/health-canada/services/food-nutrition/healthy-eating/dietary-reference-intakes/tables/reference-values-macronutrients.html'},
    {'title': 'WHO 成人活动建议', 'url': 'https://www.who.int/europe/publications/i/item/9789240014886'},
]
LIMITATION = '仅作一般健康成年人的可编辑起点，不是精确测量或医疗处方；孕期、哺乳期、疾病或特殊营养需求应采用专业建议。'


def default_targets(repo):
    raw = repo.get_meta('profile')
    facts = [f['content'] for f in repo.facts()]
    fingerprint = hashlib.sha256(json.dumps({'profile': raw, 'facts': sorted(facts), 'version': VERSION},
                                ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    result = {'status': 'unavailable', 'targets': None, 'reason': '', 'basis': '',
              'fingerprint': fingerprint, 'formula_version': VERSION, 'warnings': [],
              'sources': SOURCES, 'reviewed_on': '2026-10-10', 'estimate': None}
    if not raw:
        return {**result, 'status': 'needs_profile', 'reason': '建立基础档案后，系统会自动填入可编辑的默认目标。'}
    try:
        profile = Profile.model_validate(raw)
    except ValidationError:
        return {**result, 'status': 'needs_profile', 'reason': '基础档案不完整，请补充年龄、性别、身高、体重、目标体重和活动水平。'}
    if not 19 <= profile.age <= 78:
        return {**result, 'reason': '当前通用公式的自动估算仅覆盖 19–78 岁；其他年龄请使用专业评估的目标。'}
    if profile.sex not in ('female', 'male'):
        return {**result, 'reason': '当前公式需要男性或女性系数；不会根据“其他”性别猜测，请手动填写合适目标。'}
    if profile.nutrition_context in ('pregnant', 'breastfeeding', 'clinical'):
        return {**result, 'reason': '孕期、哺乳期或有特殊医疗营养需求时，不自动生成通用目标，请使用专业建议。'}
    # Known user restrictions override general-purpose estimation; do not infer a diagnosis.
    for fact in facts:
        if re.search(r'怀孕|孕期|哺乳|肾病|肾功能|糖尿病|进食障碍|厌食症|医生(?:要求|建议|规定)|营养师(?:要求|建议|规定)', fact):
            return {**result, 'reason': '已记录信息涉及特殊营养需求或专业限制，请手动填写专业建议的目标。'}
    height_m = profile.height_cm / 100
    bmi = profile.weight_kg / height_m ** 2
    goal_bmi = profile.goal_weight_kg / height_m ** 2
    if bmi < 18.5 or bmi >= 40 or goal_bmi < 18.5:
        return {**result, 'reason': '当前体重或目标体重超出本原型的通用估算范围，请先获得专业评估；系统不会自动给出减重额度。'}
    resting = 10 * profile.weight_kg + 6.25 * profile.height_cm - 5 * profile.age + (5 if profile.sex == 'male' else -161)
    maintenance = resting * ACTIVITY[profile.activity]
    reducing = profile.goal_weight_kg < profile.weight_kg
    deficit = min(maintenance * .1, 300) if reducing else 0
    raw_kcal = maintenance - deficit
    warnings = [LIMITATION]
    if raw_kcal > 3500 or maintenance < 1500:
        return {**result, 'reason': '计算结果超出本原型的自动估算区间，请使用专业评估的目标，不自动截断为通用数值。'}
    if raw_kcal < 1500:
        raw_kcal = 1500
        warnings.append('本原型不会自动生成低于 1500 kcal/天的目标，已缩小或取消热量缺口；这不是个体安全保证。')
    # Round weight-loss estimates upward, never enlarging the selected deficit.
    kcal = int((math.ceil(raw_kcal / 50) if reducing else round(raw_kcal / 50)) * 50)
    protein = int(round(kcal * .2 / 4))
    fat = int(round(kcal * .3 / 9))
    carbs = int(round((kcal - protein * 4 - fat * 9) / 4))
    targets = Targets(kcal=kcal, protein_g=protein, carbs_g=carbs, fat_g=fat,
                      exercise_minutes=150).model_dump()
    if profile.nutrition_context == 'unspecified':
        warnings.append('营养适用情况尚未说明；采用前请确认自己适合一般成人估算，或在档案中补充特殊情况。')
    if profile.goal_weight_kg > profile.weight_kg:
        warnings.append('目标体重高于当前体重时只提供维持估算，不自动制定增重方案。')
    mode = '温和减重起点' if reducing else '维持体重起点'
    basis = (f'Mifflin–St Jeor 静息能量约 {round(resting)} kcal/天 × 活动系数 {ACTIVITY[profile.activity]:g}；'
             f'维持估算约 {round(maintenance)} kcal/天。' +
             ('减重缺口取维持估算的 10%，最多 300 kcal/天，再按原型下限检查。' if reducing else '不额外扣减热量。') +
             '营养初始按蛋白质 20%、碳水 50%、脂肪 30% 分配并取整；每周运动为中等强度活动的可编辑起点。'
             '活动分档、缺口和计算区间是产品规则；不按期望日期倒推，也不承诺按期达到目标体重。')
    return {**result, 'status': 'ready', 'targets': targets, 'reason': mode,
            'basis': basis, 'warnings': warnings,
            'estimate': {'resting_kcal': round(resting), 'maintenance_kcal': round(maintenance),
                         'activity_factor': ACTIVITY[profile.activity],
                         'deficit_kcal': round(max(0, maintenance - kcal)) if reducing else 0, 'bmi': round(bmi, 1)}}
