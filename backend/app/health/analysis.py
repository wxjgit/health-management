from datetime import date
from statistics import mean

from ..common import clock
from .nutrition import meal_totals
from .dashboard import evidence, history_window


def meal_assessment(repo, event, today):
    totals = meal_totals(event['data']['items'])
    targets = today['targets']
    weights = repo.get_meta('preferences')['weights']
    components = {'energy': None, 'macros': None, 'satiety': None, 'adherence': None, 'snacking': None}
    notes = ['饱腹感、坚持性和加餐风险尚缺少个人反馈，暂不评分。']
    notes.extend(dict.fromkeys(i['nutrition_reason'] for i in event['data']['items'] if i.get('nutrition_reason')))
    constraints = [
        fact['content']
        for fact in repo.facts()
        if any(word in fact['content'] for word in ['过敏', '不吃', '忌口', '医生'])
    ]
    if constraints:
        notes.append('已知限制：' + '；'.join(constraints) + '。请核对实际配料；系统不能保证已完整匹配。')
    if targets and totals['complete_nutrition'] and today['totals']['complete_nutrition']:
        excess = max(0, today['totals']['kcal'] - targets['kcal']) / targets['kcal']
        components['energy'] = round(max(0, 100 - excess * 100))
        ratios = [totals[key] / max(targets[key], 1) for key in ['protein_g', 'carbs_g', 'fat_g']]
        components['macros'] = round(max(0, 100 - (max(ratios) - min(ratios)) * 100))
    coverage = sum(weights[key] for key, value in components.items() if value is not None)
    score = (
        round(sum(components[key] * weights[key] for key in components if components[key] is not None) / coverage)
        if coverage >= 70 else None
    )
    return {
        'type': 'meal',
        'event_id': event['id'],
        'title': event['raw_text'],
        'date': event['day'],
        'totals': totals,
        'items': event['data']['items'],
        'components': components,
        'coverage': coverage,
        'score': score,
        'notes': notes,
    }


def analyze(repo, end=None):
    end = end or clock.now().date()
    days = history_window(repo, 28, end)
    repo.invalidate_insights()
    paired = []
    for index in range(1, len(days)):
        previous, current = days[index - 1], days[index]
        if previous['sleep_minutes'] is not None and current['diet_complete'] and current['totals']['complete_nutrition']:
            paired.append((previous, current))
    low = [(a, b) for a, b in paired if a['sleep_minutes'] < 420]
    high = [(a, b) for a, b in paired if a['sleep_minutes'] >= 420]
    if len(low) >= 3 and len(high) >= 3:
        low_average = mean(day['totals']['kcal'] for _, day in low)
        high_average = mean(day['totals']['kcal'] for _, day in high)
        records = [
            evidence(event)
            for previous, current in paired
            for event in previous['events'] + current['events']
            if event['kind'] in ['meal', 'sleep']
        ]
        important = len(low) >= 7 and len(high) >= 7 and abs(low_average - high_average) >= 150
        data = {
            'title': '睡眠与次日已记录摄入',
            'description': (
                f'睡眠不足 7 小时后的次日平均摄入 {low_average:.0f} kcal（{len(low)} 天）；'
                f'其他组 {high_average:.0f} kcal（{len(high)} 天）。差异 {low_average-high_average:+.0f} kcal。'
            ),
            'sample_size': len(paired),
            'groups': [len(low), len(high)],
            'window': [days[0]['date'], days[-1]['date']],
            'coverage': round(len(paired) / 27 * 100),
            'confidence': '多次重复' if important else '初步观察',
            'important': important,
            'limitations': '只比较用户标为完整的记录日；周末、周期、分量估算和漏记都可能干扰。相关性不代表因果。7 小时为分析分组参数。',
            'evidence_refs': records,
            'rule_version': 'sleep-intake-v1',
            'data_version': repo.version(),
        }
        insight_id = repo.save_insight('sleep_intake', data)
        if insight_id and important:
            repo.notify(
                f'insight:{insight_id}:{end.isocalendar().week}',
                '发现值得关注的重复模式',
                data['description'] + ' 请查看样本与限制。',
            )
    complete = [day for day in days if day['diet_complete'] and day['totals']['complete_nutrition']]
    groups = [
        [day for day in complete if date.fromisoformat(day['date']).weekday() < 5],
        [day for day in complete if date.fromisoformat(day['date']).weekday() >= 5],
    ]
    if all(len(group) >= 3 for group in groups):
        averages = [mean(day['totals']['kcal'] for day in group) for group in groups]
        repo.save_insight('weekday_intake', {
            'title': '工作日与周末的饮食差异',
            'description': f'工作日平均已记录摄入 {averages[0]:.0f} kcal；周末 {averages[1]:.0f} kcal。',
            'sample_size': len(complete),
            'groups': [len(group) for group in groups],
            'window': [days[0]['date'], days[-1]['date']],
            'coverage': round(len(complete) / 28 * 100),
            'confidence': '初步观察',
            'important': False,
            'limitations': '只采用标记完整且营养数据齐全的记录。节假日、外食和估算误差可能解释差异。',
            'evidence_refs': [evidence(event) for day in complete for event in day['events'] if event['kind'] == 'meal'],
            'rule_version': 'weekday-intake-v1',
            'data_version': repo.version(),
        })
    return repo.insights()


def review(repo, period='week', end=None):
    end = end or clock.now().date()
    days = history_window(repo, 7 if period == 'week' else 1, end)
    records = [event for day in days for event in day['events']]
    weights = [day['weight_kg'] for day in days if day['weight_kg'] is not None]
    sleeps = [day['sleep_minutes'] for day in days if day['sleep_minutes'] is not None]
    complete = [day for day in days if day['diet_complete'] and day['totals']['complete_nutrition']]
    data = {
        'period': period,
        'start': days[0]['date'],
        'end': days[-1]['date'],
        'data_version': repo.version(),
        'record_count': len(records),
        'recorded_days': sum(day['record_count'] > 0 for day in days),
        'complete_days': len(complete),
        'avg_kcal': round(mean(day['totals']['kcal'] for day in complete)) if complete else None,
        'weight_change': round(weights[-1] - weights[0], 2) if len(weights) >= 2 else None,
        'avg_sleep_minutes': round(mean(sleeps)) if sleeps else None,
        'exercise_minutes': sum(day['exercise_minutes'] for day in days),
        'summary': (
            f'这段时间共有 {len(records)} 条记录；{len(complete)} 天明确标为饮食完整且营养数据齐全。'
            + ('暂不计算平均摄入，也不据此自动降低热量目标。' if not complete else '平均摄入只使用这些完整日。')
        ),
        'insights': repo.insights(),
        'evidence_refs': [evidence(event) for event in records],
    }
    repo.review(f'{period}:{end}', data)
    return data
