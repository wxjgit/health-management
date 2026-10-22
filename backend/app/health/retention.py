from collections import defaultdict
from statistics import mean

from ..common import clock
from .nutrition import meal_totals


def archive(repo, reference=None):
    reference = reference or clock.now().date()
    try:
        cutoff = reference.replace(year=reference.year - 1)
    except ValueError:
        cutoff = reference.replace(year=reference.year - 1, day=28)
    boundary = cutoff.replace(day=1).isoformat()
    groups = defaultdict(list)
    for event in repo.events(end=boundary):
        if event['day'] < boundary:
            groups[event['day'][:7]].append(event)
    if not groups:
        return []
    batches = []
    for month, events in groups.items():
        meals = [event for event in events if event['kind'] == 'meal']
        weights = [event['data']['value'] for event in events if event['kind'] == 'weight']
        items = [item for event in meals for item in event['data']['items']]
        data = {
            'month': month,
            'record_count': len(events),
            'meal_count': len(meals),
            'recorded_days': len({event['day'] for event in events}),
            'totals': meal_totals(items) if items else None,
            'avg_weight_kg': round(mean(weights), 2) if weights else None,
            'sleep_samples': sum(event['kind'] == 'sleep' for event in events),
            'exercise_minutes': sum(
                event['data']['value'] for event in events if event['kind'] == 'exercise'
            ),
            'precision': '仅月度统计；无法恢复逐餐、逐日数据或精确复核相关性',
            'version': 1,
        }
        batches.append((month, events, data))
    archived = repo.archive_months(batches)
    repo.erase_context()
    return archived
