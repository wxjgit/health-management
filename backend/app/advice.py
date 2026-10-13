"""Small, relevance-first context builder for ordinary health consultations."""
import re
from datetime import timedelta

from .clock import now
from .services import dashboard, history_window


TODAY_SCENES = {'today_attention', 'next_meal', 'food_choice', 'portion', 'general'}
CONSTRAINT = re.compile(r'过敏|忌口|不吃|不能吃|医生|营养师|疾病|怀孕|孕期|哺乳')
ANAPHORA = re.compile(r'(^|[，。！？?\s])(这|这个|这顿|那|那个|那顿|它|刚才|前面|上面|上一(?:条|顿))')


def classify_scene(question):
    if re.search(r'减脂|减重|体重.*(?:进展|变化)|目标.*(?:进展|完成)', question):
        return 'progress'
    if re.search(r'最近|这几天|近\s*[一二三四五六七八九十\d]*\s*(?:天|周)|本周', question) and re.search(
            r'吃得|饮食|摄入|怎么样|回顾|注意', question):
        return 'recent_review'
    if re.search(r'下一餐|下顿|下一顿|下餐|今晚吃|午饭吃|晚饭吃|早餐吃', question):
        return 'next_meal'
    if re.search(r'吃多少|多少合适|多大份|分量|份量', question):
        return 'portion'
    if re.search(r'能不能吃|可以吃|适合(?:我)?吗|该不该吃', question):
        return 'food_choice'
    if re.search(r'今天|今日', question) and re.search(r'吃|饮食|注意|安排|调整', question):
        return 'today_attention'
    return 'general'


def _number(value):
    return round(value, 1) if isinstance(value, float) else value


def _item(item):
    nutrients = {key: _number(item.get(key)) for key in ('kcal', 'protein_g', 'carbs_g', 'fat_g')
                 if item.get(key) is not None}
    return {
        'name': item['name'], 'quantity': _number(item.get('quantity')), 'unit': item.get('unit'),
        'size': item.get('size'), 'cooking': item.get('cooking'), 'nutrients': nutrients,
        'nutrition_status': item.get('nutrition_status', 'unknown'),
        'nutrition_source': item.get('nutrition_source', '未知'),
        'nutrition_reason': item.get('nutrition_reason'),
    }


def _meal(event):
    return {'id': event['id'], 'date': event['day'], 'meal_slot': event['data'].get('meal_slot'),
            'items': [_item(item) for item in event['data'].get('items', [])]}


def _safe_evidence(event):
    items = event['data'].get('items', [])
    foods = []
    for item in items[:4]:
        amount = item.get('quantity')
        foods.append((f'{amount:g}{item.get("unit", "")}' if amount is not None else '') + item['name'])
    return {'id': event['id'], 'date': event['day'], 'kind': 'meal',
            'text': '、'.join(foods) + ('等食物' if len(items) > 4 else '')}


def _recent_user_dialogue(repo, conversation_id, thread_id, question):
    # Raw chat is only needed to resolve anaphora. Assistant replies are never treated as facts.
    if not ANAPHORA.search(question):
        return []
    candidates = [message for message in repo.messages(conversation_id, limit=12)
                  if message['role'] == 'user' and message['thread_id'] != thread_id]
    return [{'text': message['text'][:400]} for message in candidates[-2:]]


def _feedback(repo, days=3):
    start = (now().date() - timedelta(days=days - 1)).isoformat()
    signals = []
    for event in repo.events(start=start, end=now().date().isoformat(), kind='meal', limit=30):
        for signal in event['data'].get('feedback', []):
            if signal.get('stance') == 'experience':
                signals.append({'topic': signal['topic'], 'day': str(signal.get('day') or event['day'])})
    return signals[:6]


def _day_summary(day):
    return {'date': day['date'], 'record_count': day['record_count'], 'meal_count': day['meal_count'],
            'diet_complete': day['diet_complete'], 'totals': day['totals']}


def build_advice_context(repo, question, conversation_id='main', thread_id=None):
    """Return bounded model context and the exact local records supporting it."""
    scene = classify_scene(question)
    facts = repo.facts()
    constraints = [fact['content'] for fact in facts if CONSTRAINT.search(fact['content'])][-8:]
    preferences = [fact['content'] for fact in facts if not CONSTRAINT.search(fact['content'])][-5:]
    active = repo.active_plan()
    profile = repo.get_meta('profile')
    context = {
        'question': question,
        'scene': scene,
        'now': now().isoformat(),
        'active_targets': active['data'].get('targets') if active else None,
        'constraints': constraints,
        'preferences': preferences,
        'nutrition_context': profile.get('nutrition_context', 'unspecified') if profile else 'unspecified',
        'recent_dialogue': _recent_user_dialogue(repo, conversation_id, thread_id, question),
    }
    evidence = []
    if scene in TODAY_SCENES:
        current = dashboard(repo, now().date().isoformat())
        meals = [event for event in current['events'] if event['kind'] == 'meal']
        context['today'] = {
            'date': current['date'], 'meals': [_meal(event) for event in meals],
            'totals': current['totals'], 'remaining': current['remaining'],
            'targets': current['targets'], 'meal_count': current['meal_count'],
            'record_complete': current['diet_complete'],
        }
        context['recent_feedback'] = _feedback(repo)
        evidence = [_safe_evidence(event) for event in meals]
    elif scene == 'recent_review':
        context['recent_days'] = [_day_summary(day) for day in history_window(repo, 7)
                                  if day['record_count']]
    elif scene == 'progress':
        window = history_window(repo, 28)
        context['progress'] = {
            'weights': [{'date': day['date'], 'weight_kg': day['weight_kg']}
                        for day in window if day['weight_kg'] is not None],
            'complete_diet_days': [_day_summary(day) for day in window if day['diet_complete']],
        }
        if profile:
            context['profile'] = {key: profile.get(key) for key in
                                  ('age', 'height_cm', 'weight_kg', 'goal_weight_kg', 'activity',
                                   'nutrition_context')}
    if scene == 'portion':
        if profile:
            context['profile'] = {key: profile.get(key) for key in
                                  ('age', 'height_cm', 'weight_kg', 'goal_weight_kg', 'activity',
                                   'nutrition_context')}
    context['selection_policy'] = {
        'structured_records_preferred': True,
        'assistant_messages_excluded': True,
        'dialogue_only_for_reference_resolution': True,
    }
    return context, evidence


def validate_advice(answer, context):
    text = answer.strip()
    if not text or len(text) > 1200 or '补充：' in text or 'selection_policy' in text:
        raise ValueError('建议回复格式不合格')
    allowed = set()

    def collect(value):
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, (int, float)):
            allowed.add(float(value))
        elif isinstance(value, str):
            for token in re.findall(r'\d+(?:\.\d+)?', value):
                allowed.add(float(token))
        elif isinstance(value, dict):
            for key, item in value.items():
                if key not in ('id', 'date', 'now'):
                    collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(context)
    # Formatting markers such as "1." and "2、" are not health claims.
    claim_text = re.sub(r'(^|[：:；;。！？\n])\s*[12][.、)]\s*', r'\1', text, flags=re.M)
    for token in re.findall(r'\d+(?:\.\d+)?', claim_text):
        if float(token) not in allowed:
            raise ValueError('建议回复引入了上下文之外的数值')
    if re.search(r'诊断为|患有.{0,8}(?:病|症)|停药|加药|减药|调整药量|治疗方案', text):
        raise ValueError('普通建议不能给出诊疗结论')
    today = context.get('today')
    if today and not today['record_complete']:
        if re.search(r'全天.{0,12}(?:摄入|营养).{0,10}(?:超标|不足|达标|足够)', text):
            raise ValueError('记录不完整时不能判断全天摄入')
        if re.search(r'(?:必须|需要|应该).{0,8}(?:吃完|吃满|补足).{0,8}(?:剩余|热量|额度)', text):
            raise ValueError('剩余额度不能被描述为必须吃满')
    return text


def fallback_advice(context):
    scene = context['scene']
    if scene in TODAY_SCENES:
        today = context.get('today', {})
        meals = today.get('meals', [])
        names = list(dict.fromkeys(item['name'] for meal in meals for item in meal['items']))
        if not meals:
            return '今天还没有餐食记录，所以暂时无法判断最需要调整的部分。你可以先正常安排一餐，记录食物和大致分量后再看会更具体。'
        lead = ('按目前记录的' + '、'.join(names[:3]) + ('等食物' if len(names) > 3 else '') +
                '，还不能判断今天全天是否均衡。') if not today.get('record_complete') else '按今天已确认完整的记录，可以结合目标安排后续饮食。'
        remaining = (today.get('remaining') or {}).get('kcal')
        amount = f'目前显示约剩 {remaining:g} kcal，这是相对目标的记录差额，不需要为了吃满额度刻意补足。' if remaining is not None else ''
        action = '下一餐可以优先补充你能接受的蛋白质食物和蔬菜，主食按饥饿程度安排。'
        return ''.join([lead, amount, action])
    if scene == 'recent_review':
        days = context.get('recent_days', [])
        complete = sum(day['diet_complete'] for day in days)
        return (f'最近有 {len(days)} 天留下记录，其中 {complete} 天确认完整。'
                '记录完整的天数还不多时，先补齐记录，再判断长期饮食特点会更可靠。')
    if scene == 'progress':
        weights = context.get('progress', {}).get('weights', [])
        return ('目前体重记录还不足以判断稳定趋势。' if len(weights) < 2 else
                '可以结合近期体重变化和完整饮食日观察趋势，单日波动不适合作为调整计划的依据。')
    return '目前信息不足以给出有针对性的建议。可以补充你关注的食物、分量或时间范围。'


def context_trace(context):
    today = context.get('today')
    parts = [f'识别为 {context["scene"]} 场景']
    if today is not None:
        parts.append(f'选取今天 {today["meal_count"]} 条餐食记录')
    if context.get('recent_dialogue'):
        parts.append(f'为解析指代选取同一会话 {len(context["recent_dialogue"])} 条用户消息')
    parts.append('未使用历史助手回复')
    return '；'.join(parts) + '。'
