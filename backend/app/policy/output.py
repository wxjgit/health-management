"""Render model selections against local facts; never render model prose.

Schemas only validate shape. This boundary also validates membership and keeps
dates, quantities, receipts, uncertainty and clarification under local control.
It does not try to establish the truth of arbitrary natural language with regex.
"""
from ..contracts.workflows import AdviceReply, ContextualReply
from pydantic import ValidationError
from ..common.errors import ModelOutputError, WorkflowStateError

POLICY_VERSION = 'local-output-v1'
GUIDANCE = {
    'salty': {'text': '下一餐可以少放盐和酱料，选择少盐的做法。',
              'source': 'https://www.who.int/news-room/fact-sheets/detail/sodium-reduction',
              'reviewed_on': '2026-10-09'},
    'oily': {'text': '下一餐可以选择蒸煮等做法，少用额外的油。',
             'source': 'https://www.who.int/news-room/fact-sheets/detail/healthy-diet',
             'reviewed_on': '2026-10-09'},
    'overeating': {'text': '可以先回顾这餐的分量和饱腹感，看看哪些部分让你觉得偏多。'},
    'hungry': {'text': '可以一起看这餐的分量和距离上一餐的时间，了解你没吃饱的感受。'},
}
ACK = {'salty': '你提到这次口味偏咸。', 'oily': '你觉得这次吃得有些油腻。',
       'overeating': '你觉得这次吃得偏多，这个感受也值得留意。', 'hungry': '你提到吃完仍然觉得饿。'}
QUESTION_ACK = {'salty': '你在关注吃得偏咸的问题。', 'oily': '你在关注饮食偏油的问题。',
                'overeating': '你在关注吃得偏多的问题。', 'hungry': '你在关注吃完仍然饿的问题。'}
WARM_ACK = {'salty': '你说这餐的口味有些偏咸。', 'oily': '你提到这餐吃起来有些油腻。',
            'overeating': '你提到这餐有点撑，这个感受值得留意。',
            'hungry': '你说这餐之后仍有些饿，这个感受值得留意。'}
WARM_GUIDANCE = {'salty': '下一餐可以减少盐和酱料，选少盐的做法。',
                 'oily': '后续可以选蒸煮的做法，少用额外的油。',
                 'overeating': '可以一起回顾这餐的分量和饱腹感。',
                 'hungry': '可以一起看看这餐的分量和进餐间隔，了解饥饿感。'}
SODIUM_BOUNDARY = '口味偏咸是你的感受，目前没有钠摄入数据，不能据此认定已经超标。'
MEDICAL_BOUNDARY = '涉及疾病、用药或身体不适时，这里不能判断病因或给出个体治疗方案，请咨询医生。'


def feedback_options(plan):
    """Rebuild finite variants from the catalogue, including legacy checkpoint plans."""
    result = []
    for point in plan['points']:
        topic, role = point['topic'], point['role']
        if topic not in ACK or role not in ('ack', 'guidance'):
            raise ValueError('回复要点不在本地目录中')
        prefix = 'ack' if role == 'ack' else 'guide'
        if point['point_id'] != f'{prefix}_{topic}':
            raise ValueError('回复要点标识与本地策略不一致')
        if role == 'ack':
            question = any(f['topic'] == topic and f['stance'] != 'experience' for f in plan['feedback'])
            default = QUESTION_ACK[topic] if question else ACK[topic]
            warm = default if question else WARM_ACK[topic]
        else:
            default, warm = GUIDANCE[topic]['text'], WARM_GUIDANCE[topic]
        result.append({'point_id': point['point_id'], 'variants': {'default': default, 'warm': warm}})
    return result


def default_feedback_selection(plan):
    return {'segments': [{'point_id': p['point_id'], 'variant_id': 'default'} for p in feedback_options(plan)]}


def check_phrasing(candidate, plan):
    try:
        candidate = ContextualReply.model_validate(candidate)
    except ValidationError as error:
        raise ModelOutputError() from error
    options = feedback_options(plan)
    if [s.point_id for s in candidate.segments] != [p['point_id'] for p in options]:
        raise ModelOutputError()
    return [point['variants'][segment.variant_id] for segment, point in zip(candidate.segments, options)]


def feedback_boundary(plan):
    # Never replay a free-text boundary from an old checkpoint.
    if plan.get('boundary') == MEDICAL_BOUNDARY:
        return MEDICAL_BOUNDARY
    if any(p['point_id'] == 'guide_salty' for p in plan['points']):
        return SODIUM_BOUNDARY
    return ''


def render_feedback(candidate, plan):
    texts = check_phrasing(candidate, plan) if plan['points'] else []
    boundary = feedback_boundary(plan)
    return ' '.join([*texts, *([boundary] if boundary else [])])


def clarification_question(intent, event=None, field=None, item_index=None, *, feedback_only=False,
                           has_target=False):
    """A missing field is a request for a local question, not permission to write one."""
    if feedback_only:
        return '如果想一起复盘，这次主要吃了什么？不知道或不想记录也可以。'
    if event and field:
        if field == 'occurred_at':
            return '这条记录发生在哪一天？可以说“昨天”或具体日期。'
        if field == 'items':
            return '这餐具体吃了什么？'
        if field == 'cycle_action':
            return '这次是生理周期开始、结束，还是补充备注？'
        if field == 'value':
            return {'weight': '这次体重大约多少公斤？', 'sleep': '睡了大约几小时？',
                    'exercise': '这次运动持续了多少分钟？'}[event['kind']]
        if field == 'intensity':
            return '这次运动感觉轻松、有点累，还是很累？'
        if field == 'quality':
            return '这次睡眠质量怎么样？不知道也可以。'
        if field in ('quantity', 'cooking'):
            items = event.get('items', [])
            if item_index is None or not 0 <= item_index < len(items) or not items[item_index].get('name'):
                raise ValueError('追问的食物不在草稿中')
            food = items[item_index]['name']
            if field == 'quantity':
                return f'{food}大约吃了多少？可以说“一小碗”或“半份”；不知道也可以。'
            return f'{food}主要是什么做法？不知道也可以。'
        raise ValueError('未批准的追问字段')
    if intent == 'correct':
        return '请说明这条记录要改成什么。' if has_target else '要修改哪条记录？请在历史页选择，或提供日期和内容。'
    return '请说明希望记录、修改或查询的内容；涉及历史记录时，请选择记录或说明日期和内容。'


def question_from_state(state):
    index = state.get('pending_event_index')
    events = state.get('extraction', {}).get('events', [])
    event = events[index] if index is not None and 0 <= index < len(events) else None
    fields = state.get('pending_fields', [])
    if fields and event is None:
        raise WorkflowStateError()
    return clarification_question(state['parsed']['intent'], event, fields[0] if fields else None,
                                  state.get('pending_item_index'), feedback_only=state.get('feedback_only', False),
                                  has_target=bool(state['parsed'].get('correction_id')))


def model_nutrition_uncertainty():
    return '模型粗估（未核验）；实际分量、食材和做法可能造成明显误差'


ADVICE = {
    'record_details': '可以先记录实际食物和大致分量，再结合记录具体复盘。',
    'review_portion': '可以结合这餐的实际分量、饥饿程度和饱腹感一起复盘。',
    'regular_meals': '后续保持规律进餐，按饥饿程度安排，不用通过少吃或额外运动补偿。',
    'observe_weight': '单日波动不适合作为调整计划的依据，可以继续观察稳定趋势。',
    'complete_records': '先核对是否漏记，再结合完整记录判断饮食特点会更可靠。',
    'clarify_question': '可以补充你关注的食物、分量或时间范围。',
}


def _foods(meals):
    names = list(dict.fromkeys(item['name'] for meal in meals for item in meal.get('items', [])))
    return '、'.join(names[:3]) + ('等食物' if len(names) > 3 else '')


def advice_plan(context):
    """Mandatory facts cannot be removed or reordered by a model's selections."""
    scene, view = context['scene'], context.get('view')
    facts, eligible = [], []
    if view == 'none':
        facts.append('本次没有读取健康历史，目前信息不足以给出有针对性的建议。')
        eligible = ['clarify_question']
    elif scene == 'weight_trend':
        weights = context.get('weights', [])
        window = context.get('window', {})
        facts.append(f'查询窗口 {window["start"]} 至 {window["end"]} 内有 {len(weights)} 天体重记录。'
                     if window else f'查询窗口内有 {len(weights)} 天体重记录。')
        if len(weights) < 2:
            facts.append('目前体重记录还不足以判断稳定趋势。')
        else:
            facts.append(f'窗口内首次记录为 {weights[0]["weight_kg"]:g} kg，最近一次为 {weights[-1]["weight_kg"]:g} kg。')
        eligible = ['observe_weight']
    elif scene == 'meal_reference':
        meals = context.get('resolved_records', [])
        if meals:
            facts.append(f'你指的这餐发生在 {meals[0]["date"]}，记录的食物是{_foods(meals)}。')
        facts.append('单餐记录不能代表全天饮食。')
        eligible = ['review_portion']
    elif 'today' in context:
        day = context['today']
        meals, date = day.get('meals', []), day.get('date', '所选日期')
        if not meals:
            facts.append(f'{date} 还没有餐食记录，暂时无法判断这一天最需要调整的部分。')
            eligible = ['record_details']
        else:
            facts.append(f'按目前记录，{date} 已记录 {day["meal_count"]} 餐，包含{_foods(meals)}。')
            facts.append('这一天已标记为记录完整，可以结合目标复盘。' if day['record_complete'] else
                         '这一天的记录尚未确认完整，不能判断全天摄入是否不足、超标或达标。')
            totals = day.get('totals') or {}
            if totals.get('kcal') is not None:
                label = '已知食物热量合计' if totals.get('unknown_items', 0) else '已记录食物热量合计'
                facts.append(f'{label}约 {totals["kcal"]:g} kcal。')
            targets = day.get('targets')
            if targets and targets.get('kcal') is not None:
                facts.append(f'当前生效热量目标为 {targets["kcal"]:g} kcal。')
            remaining = (day.get('remaining') or {}).get('kcal')
            if remaining is not None:
                facts.append(f'相对目标的记录差额为 {remaining:g} kcal；这不包含可能漏记的食物，不需要为了吃满额度刻意补足。')
            if not totals.get('complete_nutrition'):
                facts.append('部分营养未知，不代表零。')
            eligible = ['review_portion', 'regular_meals', 'complete_records']
    elif scene == 'recent_review':
        days = context.get('recent_days', [])
        complete = sum(day['diet_complete'] for day in days)
        window = context.get('window', {})
        prefix = f'{window["start"]} 至 {window["end"]}，' if window else '查询窗口内，'
        facts.append(f'{prefix}有 {len(days)} 天留下餐食记录，其中 {complete} 天确认完整。')
        facts.append('缺失记录不能按零摄入计算。')
        eligible = ['complete_records', 'regular_meals']
    else:
        facts.append('目前信息不足以给出有针对性的建议。')
        eligible = ['clarify_question']
    meals = [*context.get('resolved_records', []), *context.get('today', {}).get('meals', [])]
    origins = {origin for meal in meals for item in meal.get('items', [])
               for origin in item.get('nutrition_origins', [])}
    origins.update(context.get('today', {}).get('nutrition_origins', []))
    if 'model_estimate' in origins:
        facts.append(model_nutrition_uncertainty() + '。')
    if 'demo_fixture' in origins:
        facts.append('营养数值包含演示固定值，不是已核验营养库或精确测量。')
    if origins == {'user'}:
        facts.append('营养数值由用户提供，未作独立核验。')
    if scene == 'recent_review' and any(day.get('meal_count') for day in context.get('recent_days', [])):
        facts.append('汇总可能包含营养粗估，未经精确核验。')
    return {'policy_version': POLICY_VERSION, 'facts': facts,
            'options': [{'point_id': key, 'text': ADVICE[key]} for key in eligible]}


def validate_advice(candidate, context):
    try:
        selected = AdviceReply.model_validate(candidate)
    except ValidationError as error:
        raise ModelOutputError() from error
    plan = advice_plan(context)
    options = {point['point_id']: point['text'] for point in plan['options']}
    if len(selected.point_ids) != len(set(selected.point_ids)) or any(key not in options for key in selected.point_ids):
        raise ModelOutputError()
    return ''.join([*plan['facts'], *[options[key] for key in selected.point_ids]])


def fallback_advice(context):
    options = advice_plan(context)['options']
    return validate_advice({'point_ids': [options[0]['point_id']]}, context)
