"""Local response policy, grounded model phrasing, and bounded validation.

Numbers, dates, persistence receipts, questions and uncertainty boundaries never
pass through the rephraser. This guard is deliberately not a medical verifier.
"""
from ..common.errors import ModelError
from ..common.request_context import log_failure
import re
from datetime import timedelta

from ..common.clock import now
from ..contracts.workflows import ContextualReply
from ..policy.output import (
    ACK, GUIDANCE, QUESTION_ACK, MEDICAL_BOUNDARY, SODIUM_BOUNDARY,
    check_phrasing, default_feedback_selection, feedback_boundary, feedback_options,
)



def medical_question(text):
    return bool(re.search(r'血压|高血压|糖尿病|肾病|肾功能|心脏|胸痛|呼吸困难|持续.*(?:痛|吐)|用药|吃药|药量|利尿|排盐|排毒', text))


def record_receipt(records, corrected=False):
    if not records:
        return '本次没有保存记录。'
    if corrected:
        return '已修正记录并重新计算相关日期。'
    if len(records) == 1 and records[0]['kind'] == 'meal':
        event = records[0]
        day = event['day']
        today = now().date()
        day_label = '今天' if day == today.isoformat() else '昨天' if day == (today - timedelta(days=1)).isoformat() else day
        foods = []
        for item in event['data']['items'][:3]:
            quantity = item.get('quantity')
            foods.append(f'{quantity:g}{item["unit"]}{item["name"]}' if quantity is not None
                         else f'{item["name"]}（分量未知）')
        suffix = '等食物' if len(event['data']['items']) > 3 else ''
        return f'已记下{day_label}的' + '、'.join(foods) + suffix + '。'
    return f'已保存 {len(records)} 条记录。'


def make_plan(feedback, stage, include_guidance=True, medical=False, guided_topics=()):
    points = []
    seen = set()
    # At most two topics per turn: keep the reply focused, retain others in state.
    for signal in feedback[-2:]:
        topic = signal['topic']
        if topic in seen:
            continue
        seen.add(topic)
        text = ACK[topic] if signal['stance'] == 'experience' else QUESTION_ACK[topic]
        points.append({'point_id': f'ack_{topic}', 'topic': topic, 'role': 'ack', 'text': text})
        if include_guidance and not medical and topic not in guided_topics:
            points.append({'point_id': f'guide_{topic}', 'topic': topic, 'role': 'guidance', **GUIDANCE[topic]})
    return {'stage': stage, 'points': points, 'feedback': [
        {'topic': f['topic'], 'stance': f['stance']} for f in feedback[-2:]],
        'boundary': MEDICAL_BOUNDARY if medical else SODIUM_BOUNDARY
                    if any(p['point_id'] == 'guide_salty' for p in points) else ''}


def express_plan(parser, plan, *, with_selection=False):
    points = plan['points']
    selection = default_feedback_selection(plan)
    texts = check_phrasing(selection, plan) if points else []
    source = 'local'
    reason = '按本地策略回应本次反馈。'
    if points and parser.mode == 'model':
        try:
            parser.boundary.grant('contextual_reply', {'plan': plan, 'options': feedback_options(plan)})
            candidate = parser.phrase_reply(plan)
            texts = check_phrasing(candidate, plan)
            selection = ContextualReply.model_validate(candidate).model_dump()
            source = 'model'
            reason = '模型仅选择本地批准的表达版本；正文、日期、数值、保存状态与追问由程序固定。'
        except ModelError:
            # No health input, credentials or upstream error bodies in logs.
            log_failure('model_reply_fallback')
            reason = '回复润色失败或未通过校验，使用同一关注点的本地回复；记录结果不受影响。'
    if feedback_boundary(plan):
        texts.append(feedback_boundary(plan))
    result = (' '.join(texts), {'title': '针对性回应', 'detail': reason, 'source': source})
    return (*result, selection) if with_selection else result
