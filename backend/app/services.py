from collections import defaultdict
from datetime import date, timedelta
from statistics import mean
from uuid import uuid4

from .clock import now, stamp
from .nutrition import KEYS, meal_totals


def evidence(event):
    return {'id': event['id'], 'date': event['day'], 'kind': event['kind'], 'text': event['raw_text']}


def dashboard(repo, day):
    events = repo.events(start=day, end=day)
    items = [item for event in events if event['kind'] == 'meal' for item in event['data']['items']]
    totals = meal_totals(items) if items else {**dict.fromkeys(KEYS), 'unknown_items': 0, 'complete_nutrition': False, 'kcal_range': None}
    active = repo.active_plan()
    targets = active['data']['targets'] if active else None
    remaining = {key: round(targets[key] - totals[key], 1)
                 if totals[key] is not None and all(item.get(key) is not None for item in items)
                 else None for key in KEYS} if targets else None
    weight = next((e['data']['value'] for e in events if e['kind'] == 'weight'), None)
    sleep = next((e['data']['value'] for e in events if e['kind'] == 'sleep'), None)
    exercise = sum(e['data']['value'] for e in events if e['kind'] == 'exercise')
    return {
        'date': day, 'totals': totals, 'targets': targets, 'remaining': remaining,
        'weight_kg': weight, 'sleep_minutes': sleep, 'exercise_minutes': exercise,
        'meal_count': sum(e['kind'] == 'meal' for e in events), 'record_count': len(events),
        'diet_complete': repo.get_meta(f'complete:{day}', False),
        'events': events,
    }


def history_window(repo, days=28, end=None):
    end = end or now().date()
    start = end - timedelta(days=days - 1)
    return [dashboard(repo, (start + timedelta(days=i)).isoformat()) for i in range(days)]


def recommendation(repo, day=None):
    day = day or now().date().isoformat()
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
        parts.append(f'参考你 {latest["day"]} 记录的“{latest["raw_text"]}”，下一餐可换一种搭配，增加食物来源的多样性。')
    if preferences:
        parts.append('选择食物时请遵守已记录偏好与限制：' + '；'.join(p['content'] for p in preferences[-3:]))
    return ' '.join(parts), [evidence(e) for e in meals[:3]]


def meal_assessment(repo, event, today):
    totals = meal_totals(event['data']['items'])
    targets = today['targets']
    weights = repo.get_meta('preferences')['weights']
    components = {'energy': None, 'macros': None, 'satiety': None, 'adherence': None, 'snacking': None}
    notes = ['饱腹感、坚持性和加餐风险尚缺少个人反馈，暂不评分。']
    notes.extend(dict.fromkeys(i['nutrition_reason'] for i in event['data']['items'] if i.get('nutrition_reason')))
    constraints = [f['content'] for f in repo.facts() if any(w in f['content'] for w in ['过敏', '不吃', '忌口', '医生'])]
    if constraints:
        notes.append('已知限制：' + '；'.join(constraints) + '。请核对实际配料；系统不能保证已完整匹配。')
    if targets and totals['complete_nutrition'] and today['totals']['complete_nutrition']:
        excess = max(0, today['totals']['kcal'] - targets['kcal']) / targets['kcal']
        components['energy'] = round(max(0, 100 - excess * 100))
        ratios = [totals[key] / max(targets[key], 1) for key in ['protein_g', 'carbs_g', 'fat_g']]
        components['macros'] = round(max(0, 100 - (max(ratios) - min(ratios)) * 100))
    coverage = sum(weights[k] for k, value in components.items() if value is not None)
    # We deliberately avoid a misleading overall score when only 55% of dimensions are known.
    score = round(sum(components[k] * weights[k] for k in components if components[k] is not None) / coverage) if coverage >= 70 else None
    return {'type': 'meal', 'event_id': event['id'], 'title': event['raw_text'], 'date': event['day'],
            'totals': totals, 'items': event['data']['items'], 'components': components,
            'coverage': coverage, 'score': score, 'notes': notes}


def analyze(repo, end=None):
    end = end or now().date()
    days = history_window(repo, 28, end)
    repo.conn.execute("UPDATE insights SET status='stale' WHERE status='active'")
    repo.conn.commit()
    paired = []
    for index in range(1, len(days)):
        previous, current = days[index - 1], days[index]
        if previous['sleep_minutes'] is not None and current['diet_complete'] and current['totals']['complete_nutrition']:
            paired.append((previous, current))
    low = [(a, b) for a, b in paired if a['sleep_minutes'] < 420]
    high = [(a, b) for a, b in paired if a['sleep_minutes'] >= 420]
    if len(low) >= 3 and len(high) >= 3:
        a = mean(b['totals']['kcal'] for _, b in low)
        b = mean(d['totals']['kcal'] for _, d in high)
        records = [evidence(e) for x, y in paired for e in x['events'] + y['events'] if e['kind'] in ['meal', 'sleep']]
        important = len(low) >= 7 and len(high) >= 7 and abs(a - b) >= 150
        data = {'title': '睡眠与次日已记录摄入',
                'description': f'睡眠不足 7 小时后的次日平均摄入 {a:.0f} kcal（{len(low)} 天）；其他组 {b:.0f} kcal（{len(high)} 天）。差异 {a-b:+.0f} kcal。',
                'sample_size': len(paired), 'groups': [len(low), len(high)],
                'window': [days[0]['date'], days[-1]['date']], 'coverage': round(len(paired) / 27 * 100),
                'confidence': '多次重复' if important else '初步观察', 'important': important,
                'limitations': '只比较用户标为完整的记录日；周末、周期、分量估算和漏记都可能干扰。相关性不代表因果。7 小时为分析分组参数。',
                'evidence_refs': records, 'rule_version': 'sleep-intake-v1', 'data_version': repo.version()}
        insight_id = repo.save_insight('sleep_intake', data)
        if insight_id and important:
            repo.notify(f'insight:{insight_id}:{end.isocalendar().week}', '发现值得关注的重复模式', data['description'] + ' 请查看样本与限制。')
    # Weekday/weekend comparison needs several complete days in BOTH groups.
    complete = [d for d in days if d['diet_complete'] and d['totals']['complete_nutrition']]
    groups = [[d for d in complete if date.fromisoformat(d['date']).weekday() < 5],
              [d for d in complete if date.fromisoformat(d['date']).weekday() >= 5]]
    if all(len(g) >= 3 for g in groups):
        averages = [mean(d['totals']['kcal'] for d in g) for g in groups]
        repo.save_insight('weekday_intake', {
            'title': '工作日与周末的饮食差异',
            'description': f'工作日平均已记录摄入 {averages[0]:.0f} kcal；周末 {averages[1]:.0f} kcal。',
            'sample_size': len(complete), 'groups': [len(g) for g in groups], 'window': [days[0]['date'], days[-1]['date']],
            'coverage': round(len(complete) / 28 * 100), 'confidence': '初步观察', 'important': False,
            'limitations': '只采用标记完整且营养数据齐全的记录。节假日、外食和估算误差可能解释差异。',
            'evidence_refs': [evidence(e) for d in complete for e in d['events'] if e['kind'] == 'meal'],
            'rule_version': 'weekday-intake-v1', 'data_version': repo.version(),
        })
    return repo.insights()


def review(repo, period='week', end=None):
    end = end or now().date()
    days = history_window(repo, 7 if period == 'week' else 1, end)
    records = [e for d in days for e in d['events']]
    weights = [d['weight_kg'] for d in days if d['weight_kg'] is not None]
    sleeps = [d['sleep_minutes'] for d in days if d['sleep_minutes'] is not None]
    complete = [d for d in days if d['diet_complete'] and d['totals']['complete_nutrition']]
    data = {'period': period, 'start': days[0]['date'], 'end': days[-1]['date'], 'data_version': repo.version(),
            'record_count': len(records), 'recorded_days': sum(d['record_count'] > 0 for d in days),
            'complete_days': len(complete), 'avg_kcal': round(mean(d['totals']['kcal'] for d in complete)) if complete else None,
            'weight_change': round(weights[-1] - weights[0], 2) if len(weights) >= 2 else None,
            'avg_sleep_minutes': round(mean(sleeps)) if sleeps else None,
            'exercise_minutes': sum(d['exercise_minutes'] for d in days),
            'summary': f'这段时间共有 {len(records)} 条记录；{len(complete)} 天明确标为饮食完整且营养数据齐全。' + ('暂不计算平均摄入，也不据此自动降低热量目标。' if not complete else '平均摄入只使用这些完整日。'),
            'insights': repo.insights(), 'evidence_refs': [evidence(e) for e in records]}
    repo.review(f'{period}:{end}', data)
    return data


def plan_draft(repo, targets, note=''):
    active = repo.active_plan()
    current_targets = active['data']['targets'] if active else None
    targets = targets or current_targets
    if targets is None:
        raise ValueError('请先填写你希望采用的热量与营养目标；当前原型不会仅凭体重自动计算目标。')
    anchor = active['data'].get('confirmed_anchor', current_targets) if active else targets
    change = {key: round((targets[key] - current_targets[key]) / max(current_targets[key], 1) * 100, 1) for key in targets} if current_targets else {}
    cumulative = {key: abs(targets[key] - anchor[key]) / max(anchor[key], 1) * 100 for key in targets}
    needs_confirmation = active is None or any(abs(v) > 5 for v in change.values()) or any(v > 5 for v in cumulative.values())
    recent = repo.events(limit=30)
    sleep_events = [e for e in recent if e['kind'] == 'sleep']
    exercise_events = [e for e in recent if e['kind'] == 'exercise']
    constraints = [f['content'] for f in repo.facts()]
    actual_durations = [e['data']['value'] for e in exercise_events[:5]]
    duration = round(mean(actual_durations)) if actual_durations else 20
    session_minutes = round(targets['exercise_minutes'] / 4)
    start = now().date() + timedelta(days=1)
    schedule = []
    for index in range(7):
        day = (start + timedelta(days=index)).isoformat()
        food = '三餐规律进食；每餐安排蛋白质来源、蔬菜与适量主食，记录实际分量。'
        if constraints:
            food += ' 遵守：' + '；'.join(constraints[-3:])
        if index == 0 and sleep_events and sleep_events[0]['data']['value'] < 420:
            food += ' 最近一次睡眠较短，提前安排方便准备的餐食，减少临时决策。'
        activity = f'按你设置的每周目标分为 4 次，本次目标约 {session_minutes} 分钟；近期记录单次参考 {duration} 分钟，按当日状态调整，不能完成时记录原因。' if targets['exercise_minutes'] and index in [0, 2, 4, 6] else '恢复日，保持日常活动，不用补偿性运动抵消饮食。'
        schedule.append({'date': day, 'food': food, 'exercise': activity})
    return {
        'targets': targets, 'schedule': schedule, 'next_meal': recommendation(repo)[0],
        'tomorrow_focus': schedule[0]['food'], 'notes': note or '数值目标由你提供；安排参考近期记录，不把缺失数据当成零。',
        'changes': change, 'needs_confirmation': needs_confirmation,
        'confirmed_anchor': targets if needs_confirmation else anchor,
        'data_version': repo.version(), 'base_plan_id': active['id'] if active else None,
        'evidence_refs': [evidence(e) for e in recent[:12]], 'strategy_version': 'user-targets-v1',
    }


def suggest_weights(repo):
    active = repo.active_plan()
    if not active:
        return None
    days = [d for d in history_window(repo, 14) if d['diet_complete'] and d['totals']['complete_nutrition']]
    if len(days) < 7:
        return None
    target = active['data']['targets']['kcal']
    deviations = [abs(d['totals']['kcal'] - target) / target for d in days]
    deviation = mean(deviations)
    current = repo.get_meta('preferences')['weights']
    if deviation < .15 or current['macros'] < 5 or current['energy'] >= 40:
        return None
    weights = {**current, 'energy': current['energy'] + 5, 'macros': current['macros'] - 5}
    return {'weights': weights, 'reason': f'近 14 天有 {len(days)} 个完整饮食日，已记录热量与当前目标的平均绝对偏差约 {deviation*100:.0f}%。可尝试提高热量适配的关注权重，保持其他未充分观察的维度不变。',
            'limitations': '这是可撤回的规则建议，不证明修改权重就能改善减脂效果，也不修改热量目标。',
            'evidence_refs': [evidence(e) for d in days for e in d['events'] if e['kind'] == 'meal'],
            'data_version': repo.version(), 'preferences_version': repo.get_meta('preferences_version', 0),
            'rule_version': 'budget-focus-v1'}


def archive(repo, reference=None):
    reference = reference or now().date()
    try:
        cutoff = reference.replace(year=reference.year - 1)
    except ValueError:
        cutoff = reference.replace(year=reference.year - 1, day=28)
    # Only full expired months can be archived. Every event in such a month is past one year.
    boundary = cutoff.replace(day=1).isoformat()
    groups = defaultdict(list)
    for event in repo.events(end=boundary):
        if event['day'] < boundary:
            groups[event['day'][:7]].append(event)
    if not groups:
        return []
    archived = []
    from .repository import dump
    with repo.lock, repo.conn:
        for month, events in groups.items():
            meals = [e for e in events if e['kind'] == 'meal']
            weights = [e['data']['value'] for e in events if e['kind'] == 'weight']
            items = [i for e in meals for i in e['data']['items']]
            data = {'month': month, 'record_count': len(events), 'meal_count': len(meals),
                    'recorded_days': len({e['day'] for e in events}), 'totals': meal_totals(items) if items else None,
                    'avg_weight_kg': round(mean(weights), 2) if weights else None,
                    'sleep_samples': sum(e['kind'] == 'sleep' for e in events),
                    'exercise_minutes': sum(e['data']['value'] for e in events if e['kind'] == 'exercise'),
                    'precision': '仅月度统计；无法恢复逐餐、逐日数据或精确复核相关性', 'version': 1}
            repo.conn.execute('INSERT OR REPLACE INTO monthly_summaries VALUES (?,?,?)', (month, dump(data), stamp()))
            check = repo.conn.execute('SELECT data FROM monthly_summaries WHERE month=?', (month,)).fetchone()
            import json
            if json.loads(check['data'])['record_count'] != len(events):
                raise ValueError('月度摘要校验失败，保留原记录')
            for event in events:
                repo.conn.execute('DELETE FROM facts WHERE source_event=?', (event['id'],))
                repo.conn.execute('DELETE FROM events WHERE id=?', (event['id'],))
            for key in [r['key'] for r in repo.conn.execute("SELECT key FROM meta WHERE key LIKE 'complete:%'") if r['key'][9:].startswith(month)]:
                repo.conn.execute('DELETE FROM meta WHERE key=?', (key,))
            archived.append(month)
        repo._bump()
    repo.erase_context()
    return archived
