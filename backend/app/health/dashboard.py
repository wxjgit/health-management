from datetime import timedelta

from ..common import clock
from .nutrition import KEYS, meal_totals


def evidence(event):
    return {'id': event['id'], 'date': event['day'], 'kind': event['kind'], 'text': event['raw_text']}


def dashboard(repo, day):
    events = repo.events(start=day, end=day)
    items = [item for event in events if event['kind'] == 'meal' for item in event['data']['items']]
    totals = meal_totals(items) if items else {
        **dict.fromkeys(KEYS),
        'unknown_items': 0,
        'complete_nutrition': False,
        'kcal_range': None,
    }
    active = repo.active_plan()
    targets = active['data']['targets'] if active else None
    remaining = {
        key: round(targets[key] - totals[key], 1)
        if totals[key] is not None and all(item.get(key) is not None for item in items)
        else None
        for key in KEYS
    } if targets else None
    weight = next((e['data']['value'] for e in events if e['kind'] == 'weight'), None)
    sleep = next((e['data']['value'] for e in events if e['kind'] == 'sleep'), None)
    exercise = sum(e['data']['value'] for e in events if e['kind'] == 'exercise')
    return {
        'date': day,
        'totals': totals,
        'targets': targets,
        'remaining': remaining,
        'weight_kg': weight,
        'sleep_minutes': sleep,
        'exercise_minutes': exercise,
        'meal_count': sum(e['kind'] == 'meal' for e in events),
        'record_count': len(events),
        'diet_complete': repo.get_meta(f'complete:{day}', False),
        'events': events,
    }


def history_window(repo, days=28, end=None):
    end = end or clock.now().date()
    start = end - timedelta(days=days - 1)
    return [dashboard(repo, (start + timedelta(days=i)).isoformat()) for i in range(days)]


def recommendation(repo, day=None):
    day = day or clock.now().date().isoformat()
    today = dashboard(repo, day)
    meals = [e for e in repo.events(limit=30) if e['kind'] == 'meal']
    preferences = repo.facts()
    parts = []
    if today['remaining'] and today['remaining']['kcal'] is not None:
        left = today['remaining']['kcal']
        parts.append(f'按已记录摄入，今天约剩 {left:g} kcal；这不包含可能漏记的食物。')
        if today['remaining']['protein_g'] is not None and today['remaining']['protein_g'] > 15:
            parts.append('下一餐可优先安排你能接受的蛋白质食物，再搭配蔬菜和适量主食。')
        elif left < 0:
            parts.append('今天已记录摄入超过目标，下一餐仍保持规律进餐，选择容易控制分量的组合。')
    else:
        reason = '尚未确认热量目标' if not today['targets'] else '当前记录存在热量未知的食物'
        parts.append(f'下一餐可用蛋白质食物、蔬菜和主食搭配；{reason}，暂不计算具体额度。')
    if meals:
        latest = meals[0]
        parts.append(
            f'参考你 {latest["day"]} 记录的“{latest["raw_text"]}”，下一餐可换一种搭配，增加食物来源的多样性。'
        )
    if preferences:
        parts.append('选择食物时请遵守已记录偏好与限制：' + '；'.join(p['content'] for p in preferences[-3:]))
    return ' '.join(parts), [evidence(e) for e in meals[:3]]
