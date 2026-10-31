"""按最小必要原则构造普通健康咨询上下文。"""
import re
from datetime import timedelta

from ..common.clock import date_from_text, now
from .dashboard import dashboard, history_window
from ..policy.output import fallback_advice, validate_advice  # Public compatibility imports.
from .fact_resolution import fact_projection, require_resolved


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


def _origins(items):
    return sorted({origin for item in items for origin in (
        list(item.get('nutrition_sources', {}).values()) or [item.get('source', 'unknown')])})


def _item(item):
    nutrients = {key: _number(item.get(key)) for key in ('kcal', 'protein_g', 'carbs_g', 'fat_g')
                 if item.get(key) is not None}
    return {
        'name': item['name'], 'quantity': _number(item.get('quantity')), 'unit': item.get('unit'),
        'size': item.get('size'), 'cooking': item.get('cooking'), 'nutrients': nutrients,
        'nutrition_status': item.get('nutrition_status', 'unknown'),
        'nutrition_source': item.get('nutrition_source', '未知'),
        'nutrition_reason': item.get('nutrition_reason'),
        'nutrition_origins': _origins([item]),
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


def build_advice_context(repo, question, conversation_id='main', thread_id=None, *, view=None, bound=None, scope_text=None, window_start=None, window_end=None):
    """Return bounded model context and the exact local records supporting it."""
    from ..workflows.chat.policy import ChatPlanPolicy
    view = view or ChatPlanPolicy.advice_view(question, bound)
    scene = {'none': 'general', 'weight_trend': 'weight_trend', 'meal_reference': 'meal_reference', 'diet_week': 'recent_review',
             'diet_portion': 'portion'}.get(view, classify_scene(question))
    context = {'question': question, 'scene': scene, 'view': view, 'now': now().isoformat()}
    if view == 'none':
        return context, []
    if window_start is not None and window_end is not None:
        limit = {'weight_trend': 28, 'diet_week': 7}.get(view, 1)
        if not 0 <= (window_end - window_start).days < limit:
            from ..common.errors import DomainValidationError
            raise DomainValidationError(code='policy_scope_denied')
    end = window_end or date_from_text(question).date()
    if view == 'weight_trend':
        days = (end - window_start).days + 1 if window_start else ChatPlanPolicy.view_days(scope_text or question, view)
        window = history_window(repo, days, end)
        context['window'] = {'start': (end - timedelta(days=days - 1)).isoformat(), 'end': end.isoformat()}
        context['weights'] = [{'date': day['date'], 'weight_kg': day['weight_kg']}
                              for day in window if day['weight_kg'] is not None]
        return context, []
    facts = repo.resolve_facts(purpose='advice')
    require_resolved(facts)
    constraints = [fact_projection(fact) for fact in facts['constraints']]
    preferences = [fact_projection(fact) for fact in facts['preferences']][-5:]
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
        current = dashboard(repo, end.isoformat())
        meals = [event for event in current['events'] if event['kind'] == 'meal'][:10]
        context['today'] = {
            'date': current['date'], 'meals': [_meal(event) for event in meals],
            'totals': current['totals'], 'remaining': current['remaining'],
            'targets': {k: v for k, v in current['targets'].items()
                        if k in ('kcal', 'protein_g', 'carbs_g', 'fat_g')} if current['targets'] else None,
            'meal_count': current['meal_count'],
            'record_complete': current['diet_complete'],
            'nutrition_origins': _origins([item for event in current['events'] if event['kind'] == 'meal'
                                           for item in event['data'].get('items', [])]),
        }
        context['recent_feedback'] = _feedback(repo, end=end)
        evidence = [_safe_evidence(event) for event in meals]
    elif view == 'diet_week':
        days = (end - window_start).days + 1 if window_start else ChatPlanPolicy.view_days(scope_text or question, view)
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


def context_trace(context):
    today = context.get('today')
    parts = [f'识别为 {context["scene"]} 场景']
    if today is not None:
        parts.append(f'选取 {today["date"]} 的 {today["meal_count"]} 条餐食记录')
    if context.get('resolved_records'):
        parts.append('指代由本地绑定到单条结构化记录')
    parts.append('未发送历史聊天原文')
    return '；'.join(parts) + '。'
