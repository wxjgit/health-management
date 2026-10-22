"""Local response policy, grounded model phrasing, and bounded validation.

Numbers, dates, persistence receipts, questions and uncertainty boundaries never
pass through the rephraser. This guard is deliberately not a medical verifier.
"""
import logging
import re
from datetime import timedelta

from ..common.clock import now
from ..contracts.workflows import ContextualReply

logger = logging.getLogger(__name__)

# Versioned, deliberately small guidance catalogue, not a nutritional database.
GUIDANCE = {
    'salty': {
        'text': '下一餐可以少放盐和酱料，选择少盐的做法。',
        'source': 'https://www.who.int/news-room/fact-sheets/detail/sodium-reduction',
        'reviewed_on': '2026-10-09',
    },
    'oily': {
        'text': '下一餐可以选择蒸煮等做法，少用额外的油。',
        'source': 'https://www.who.int/news-room/fact-sheets/detail/healthy-diet',
        'reviewed_on': '2026-10-09',
    },
    'overeating': {'text': '可以先回顾这餐的分量和饱腹感，看看哪些部分让你觉得偏多。'},
    'hungry': {'text': '可以一起看这餐的分量和距离上一餐的时间，了解你没吃饱的感受。'},
}
ACK = {'salty': '你提到这次口味偏咸。', 'oily': '你觉得这次吃得有些油腻。',
       'overeating': '你觉得这次吃得偏多，这个感受也值得留意。',
       'hungry': '你提到吃完仍然觉得饿。'}
ANCHORS = {'salty': r'咸|盐|口味.*重', 'oily': r'油|腻',
           'overeating': r'偏多|吃多|撑|饱|分量', 'hungry': r'饿|没吃饱|不饱'}
SODIUM_BOUNDARY = '口味偏咸是你的感受，目前没有钠摄入数据，不能据此认定已经超标。'
MEDICAL_BOUNDARY = '涉及疾病、用药或身体不适时，这里不能判断病因或给出个体治疗方案，请咨询医生。'


def medical_question(text):
    return bool(re.search(r'血压|高血压|糖尿病|肾病|肾功能|心脏|胸痛|呼吸困难|持续.*(?:痛|吐)|用药|吃药|药量|利尿|排盐|排毒', text))


def record_receipt(records, corrected=False):
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
        text = ACK[topic] if signal['stance'] == 'experience' else {
            'salty': '你在关注吃得偏咸的问题。', 'oily': '你在关注饮食偏油的问题。',
            'overeating': '你在关注吃得偏多的问题。', 'hungry': '你在关注吃完仍然饿的问题。',
        }[topic]
        points.append({'point_id': f'ack_{topic}', 'topic': topic, 'role': 'ack', 'text': text})
        if include_guidance and not medical and topic not in guided_topics:
            points.append({'point_id': f'guide_{topic}', 'topic': topic, 'role': 'guidance', **GUIDANCE[topic]})
    return {'stage': stage, 'points': points, 'feedback': [
        {'topic': f['topic'], 'stance': f['stance']} for f in feedback[-2:]],
        'boundary': MEDICAL_BOUNDARY if medical else SODIUM_BOUNDARY
                    if any(p['point_id'] == 'guide_salty' for p in points) else ''}


def check_phrasing(candidate, plan):
    """Reject unapproved points, figures, clinical claims and generic detours."""
    candidate = ContextualReply.model_validate(candidate)
    if [s.point_id for s in candidate.segments] != [p['point_id'] for p in plan['points']]:
        raise ValueError('回复要点与本地策略不一致')
    for segment, point in zip(candidate.segments, plan['points']):
        text = segment.text.strip()
        if len(text) > 100 or '\n' in text or re.search(r'[?？]', text) or not re.search(ANCHORS[point['topic']], text):
            raise ValueError('回复偏离当前关注点或过长')
        if re.search(r'\d|(?<![上下这那每])[一二两三四五六七八九十百千万零半]+\s*(?:克|升|杯|碗|份|餐|分钟|小时|天|周|次)', text):
            raise ValueError('润色层不能提供数值')
        if re.search(r'昨天|今天|前天|昨晚|明天|后天|日期|已保存|已记录|记录了|已修正|已纠正|没有保存|未保存|已经记', text):
            raise ValueError('润色层不能改变日期或保存状态')
        if re.search(r'超标|过量|缺乏|不足|一定|肯定|证明|导致|保证|正常|不健康|不自律|肥胖|罪恶|补偿|'
                     r'排盐|排毒|喝水|断食|禁食|催吐|利尿|药|钾盐|代盐|低钠盐|血压|肾|病|治疗|诊断|'
                     r'蛋白质|碳水|主食|蔬菜|热量|kcal|卡路里|千卡|毫克|公斤|剩余|额度|目标|'
                     r'不吃|少吃|跳过|不必吃|不用吃|无需吃|加餐|水果|牛奶|鸡蛋|燕麦|摄入|含量|含钠|含盐|'
                     r'运动|跑步|锻炼|燃烧|代谢|中毒|风险|浮肿|水肿|(?:喝|饮).{0,8}(?:水|液)|补充.{0,5}水|https?://|<|```', text, re.I):
            raise ValueError('回复包含未授权的建议或事实')
        if point['role'] == 'ack':
            if re.search(r'建议|可以|不妨|需要|必须|应该', text):
                raise ValueError('反馈确认不能新增行动建议')
            if not re.search(r'觉得|感受|感觉|提到|反馈|关注|说|担心', text):
                raise ValueError('不能把主观反馈变成确定判断')
            if point['text'].startswith('你在关注') and not re.search(r'关注|问题|如果', text):
                raise ValueError('不能把假设问题变成个人经历')
        elif point['topic'] == 'salty':
            if not re.search(r'少|减少|低|控制', text) or not re.search(r'盐|酱料', text):
                raise ValueError('减盐建议的含义丢失')
        elif point['topic'] == 'oily':
            if not re.search(r'蒸|煮|少油|少用.*油', text):
                raise ValueError('做法建议的含义丢失')
        elif not re.search(r'回顾|看|复盘|了解', text) or not re.search(r'分量|饱腹|时间|间隔', text):
            raise ValueError('反馈复盘建议的含义丢失')
    return [s.text.strip() for s in candidate.segments]


def express_plan(parser, plan):
    points = plan['points']
    texts = [p['text'] for p in points]
    source = 'local'
    reason = '按本地策略回应本次反馈。'
    if points and parser.mode == 'model':
        try:
            parser.boundary.grant('contextual_reply', {'plan': plan})
            candidate = parser.phrase_reply(plan)
            texts = check_phrasing(candidate, plan)
            source = 'model'
            reason = '模型仅表达已批准要点；日期、数值、保存状态与追问由程序固定。'
        except ValueError:
            # No health input, credentials or upstream error bodies in logs.
            logger.warning('contextual_reply_fallback')
            reason = '回复润色失败或未通过校验，使用同一关注点的本地回复；记录结果不受影响。'
    if plan['boundary']:
        texts.append(plan['boundary'])
    return ' '.join(texts), {'title': '针对性回应', 'detail': reason, 'source': source}
