"""Small, relevance-first context builder for ordinary health consultations."""
import re
from datetime import timedelta

from ..common.clock import date_from_text, now
from .dashboard import dashboard, history_window
from .safety import unsafe_generated_text


TODAY_SCENES = {'today_attention', 'next_meal', 'food_choice', 'portion', 'general'}
CONSTRAINT = re.compile(r'过敏|忌口|不吃|不能吃|医生|营养师|疾病|怀孕|孕期|哺乳')


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
    return {'date': event['day'], 'meal_slot': event['data'].get('meal_slot'),
            'items': [_item(item) for item in event['data'].get('items', [])[:10]]}


def _safe_evidence(event):
    items = event['data'].get('items', [])
    foods = []
    for item in items[:4]:
        amount = item.get('quantity')
        foods.append((f'{amount:g}{item.get("unit", "")}' if amount is not None else '') + item['name'])
    return {'id': event['id'], 'date': event['day'], 'kind': 'meal',
            'text': '、'.join(foods) + ('等食物' if len(items) > 4 else '')}


def _feedback(repo, days=1, end=None):
    end = end or now().date()
    start = (end - timedelta(days=days - 1)).isoformat()
    signals = []
    for event in repo.events(start=start, end=end.isoformat(), kind='meal', limit=30):
        for signal in event['data'].get('feedback', []):
            if signal.get('stance') == 'experience':
                signals.append({'topic': signal['topic'], 'day': str(signal.get('day') or event['day'])})
    return signals[:6]


def _day_summary(day):
    return {'date': day['date'], 'meal_count': day['meal_count'],
            'diet_complete': day['diet_complete'], 'totals': day['totals']}


def build_advice_context(repo, question, conversation_id='main', thread_id=None, *, view=None, bound=None, scope_text=None):
    """Return bounded model context and the exact local records supporting it."""
    from ..workflows.policy import PlanHarness
    view = view or PlanHarness.advice_view(question, bound)
    scene = {'none': 'general', 'weight_trend': 'weight_trend', 'meal_reference': 'meal_reference', 'diet_week': 'recent_review',
             'diet_portion': 'portion'}.get(view, classify_scene(question))
    context = {'question': question, 'scene': scene, 'view': view, 'now': now().isoformat()}
    if view == 'none':
        return context, []
    if view == 'weight_trend':
        days = PlanHarness.view_days(scope_text or question, view)
        end = date_from_text(question).date()
        window = history_window(repo, days, end)
        context['window'] = {'start': (end - timedelta(days=days - 1)).isoformat(), 'end': end.isoformat()}
        context['weights'] = [{'date': day['date'], 'weight_kg': day['weight_kg']}
                              for day in window if day['weight_kg'] is not None]
        return context, []
    facts = repo.facts()
    constraints = [fact['content'][:500] for fact in facts if CONSTRAINT.search(fact['content'])][-8:]
    preferences = [fact['content'][:500] for fact in facts if not CONSTRAINT.search(fact['content'])][-5:]
    active = repo.active_plan()
    profile = repo.get_meta('profile')
    context.update({
        'active_targets': {k: v for k, v in active['data'].get('targets', {}).items()
                           if k in ('kcal', 'protein_g', 'carbs_g', 'fat_g')} if active else None,
        'constraints': constraints,
        'preferences': preferences,
        'nutrition_context': profile.get('nutrition_context', 'unspecified') if profile else 'unspecified',
    })
    evidence = []
    if view in ('diet_today', 'diet_portion'):
        current = dashboard(repo, date_from_text(question).date().isoformat())
        meals = [event for event in current['events'] if event['kind'] == 'meal'][:10]
        context['today'] = {
            'date': current['date'], 'meals': [_meal(event) for event in meals],
            'totals': current['totals'], 'remaining': current['remaining'],
            'targets': {k: v for k, v in current['targets'].items()
                        if k in ('kcal', 'protein_g', 'carbs_g', 'fat_g')} if current['targets'] else None,
            'meal_count': current['meal_count'],
            'record_complete': current['diet_complete'],
        }
        context['recent_feedback'] = _feedback(repo, end=date_from_text(question).date())
        evidence = [_safe_evidence(event) for event in meals]
    elif view == 'diet_week':
        days = PlanHarness.view_days(scope_text or question, view)
        end = date_from_text(question).date()
        context['window'] = {'start': (end - timedelta(days=days - 1)).isoformat(), 'end': end.isoformat()}
        context['recent_days'] = [_day_summary(day) for day in history_window(repo, days, end)
                                  if day['meal_count']]
    if scene == 'portion':
        if profile:
            context['profile'] = {key: profile.get(key) for key in
                                  ('age', 'height_cm', 'weight_kg', 'goal_weight_kg', 'activity',
                                   'nutrition_context')}
    if bound and bound['kind'] == 'meal' and view in ('diet_today', 'diet_portion', 'meal_reference'):
        context['resolved_records'] = [_meal(bound)]
        evidence = list({e['id']: e for e in [*evidence, _safe_evidence(bound)]}.values())
    context['selection_policy'] = {
        'structured_records_preferred': True,
        'assistant_messages_excluded': True,
        'references_resolved_locally': True,
    }
    return context, evidence


def validate_advice(answer, context):
    text = answer.strip()
    if unsafe_generated_text(text):
        raise ValueError('建议包含未授权的医疗或补偿行为内容')
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
    if scene == 'meal_reference':
        return '已定位到你指的这餐。单餐记录不能代表全天饮食；可以结合食物搭配、实际分量和饥饿感判断。'
    if scene == 'weight_trend':
        weights = context.get('weights', [])
        return ('目前体重记录还不足以判断稳定趋势。' if len(weights) < 2 else
                f'查询窗口内有 {len(weights)} 天体重记录。单日波动不适合作为调整计划的依据，可以继续观察稳定趋势。')
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
    if context.get('resolved_records'):
        parts.append('指代由本地绑定到单条结构化记录')
    parts.append('未发送历史聊天原文')
    return '；'.join(parts) + '。'
