from datetime import timedelta
from statistics import mean

from ..common import clock
from .targets import default_targets
from .dashboard import evidence, history_window, recommendation
from ..privacy.provenance import tracked
from .safety import require_plan_eligibility
from .fact_resolution import fact_projection, require_resolved
from .facts import FACT_POLICY_VERSION


@tracked
def plan_draft(repo, targets, note='', target_source='manual', suggestion_fingerprint=None):
    require_plan_eligibility(repo, targets, note)
    active = repo.active_plan()
    current_targets = active['data']['targets'] if active else None
    inherited = targets is None and current_targets and target_source != 'system_default'
    target_metadata = None
    if targets is None and current_targets and target_source != 'system_default':
        targets = current_targets
        target_source = active['data'].get('target_source', 'manual')
        target_metadata = active['data'].get('target_metadata')
    elif targets is None or target_source == 'system_default':
        suggestion = default_targets(repo)
        if suggestion['status'] != 'ready':
            raise ValueError(suggestion['reason'])
        if suggestion_fingerprint and suggestion_fingerprint != suggestion['fingerprint']:
            raise ValueError('基础资料或限制已变化，请刷新并重新采用系统默认；你的手动目标没有被覆盖。')
        if targets is not None and targets != suggestion['targets']:
            raise ValueError('提交值不是当前系统默认；修改过的目标请作为手动目标提交。')
        targets = suggestion['targets']
        target_source = 'system_default'
        target_metadata = suggestion
    require_plan_eligibility(repo, targets, note)
    anchor = active['data'].get('confirmed_anchor', current_targets) if active else targets
    change = {
        key: round((targets[key] - current_targets[key]) / max(current_targets[key], 1) * 100, 1)
        for key in targets
    } if current_targets else {}
    cumulative = {key: abs(targets[key] - anchor[key]) / max(anchor[key], 1) * 100 for key in targets}
    needs_confirmation = (
        active is None
        or any(abs(value) > 5 for value in change.values())
        or any(value > 5 for value in cumulative.values())
    )
    recent = repo.events(limit=30)
    sleep_events = [event for event in recent if event['kind'] == 'sleep']
    exercise_events = [event for event in recent if event['kind'] == 'exercise']
    actual_durations = [event['data']['value'] for event in exercise_events[:5]]
    duration = round(mean(actual_durations)) if actual_durations else 20
    session_minutes = round(targets['exercise_minutes'] / 4)
    start = clock.now().date() + timedelta(days=1)
    schedule = []
    for index in range(7):
        day = (start + timedelta(days=index)).isoformat()
        view = repo.resolve_facts(day, 'plan')
        require_resolved(view)
        if any(fact['enforcement'] == 'clinical_review' for fact in view['constraints']):
            raise ValueError('计划日期存在需专业评估的信息，请先核对，暂不生成普通计划。')
        constraints = [fact['content'] for fact in view['effective']]
        food = '三餐规律进食；每餐安排蛋白质来源、蔬菜与适量主食，记录实际分量。'
        if constraints:
            food += ' 参考：' + '；'.join(constraints)
        if index == 0 and sleep_events and sleep_events[0]['data']['value'] < 420:
            food += ' 最近一次睡眠较短，提前安排方便准备的餐食，减少临时决策。'
        activity = (
            f'按你设置的每周目标分为 4 次，本次目标约 {session_minutes} 分钟；近期记录单次参考 {duration} 分钟，按当日状态调整，不能完成时记录原因。'
            if targets['exercise_minutes'] and index in [0, 2, 4, 6]
            else '恢复日，保持日常活动，不用补偿性运动抵消饮食。'
        )
        schedule.append({'date': day, 'food': food, 'exercise': activity,
                         'facts': [fact_projection(fact) for fact in view['effective']]})
    return {
        '_target_sources': ([repo.provenance_node('plan', active['id'], part='targets')] if inherited else
                            [repo.provenance_node('meta', 'profile'),
                             *[repo.provenance_node('fact', fact['id']) for fact in repo.facts()]]
                            if target_source == 'system_default' else []),
        'targets': targets,
        'schedule': schedule,
        'next_meal': recommendation(repo)[0],
        'tomorrow_focus': schedule[0]['food'],
        'notes': note or (
            '目标是根据基础档案生成的可编辑初始估算，不是精确测量或医疗处方；未按期望日期倒推热量。'
            if target_source == 'system_default'
            else '数值目标由你提供；安排参考近期记录，不把缺失数据当成零。'
        ),
        'target_source': target_source,
        'target_metadata': target_metadata,
        'changes': change,
        'needs_confirmation': needs_confirmation,
        'confirmed_anchor': targets if needs_confirmation else anchor,
        'data_version': repo.version(),
        'base_plan_id': active['id'] if active else None,
        'evidence_refs': [evidence(event) for event in recent[:12]],
        'strategy_version': 'editable-targets-facts-v3',
        'fact_policy_version': FACT_POLICY_VERSION,
        'fact_reference_date': clock.now().date().isoformat(),
    }


def suggest_weights(repo):
    active = repo.active_plan()
    if not active:
        return None
    days = [
        day for day in history_window(repo, 14)
        if day['diet_complete'] and day['totals']['complete_nutrition']
    ]
    if len(days) < 7:
        return None
    target = active['data']['targets']['kcal']
    deviations = [abs(day['totals']['kcal'] - target) / target for day in days]
    deviation = mean(deviations)
    current = repo.get_meta('preferences')['weights']
    if deviation < .15 or current['macros'] < 5 or current['energy'] >= 40:
        return None
    weights = {**current, 'energy': current['energy'] + 5, 'macros': current['macros'] - 5}
    return {
        'weights': weights,
        'reason': (
            f'近 14 天有 {len(days)} 个完整饮食日，已记录热量与当前目标的平均绝对偏差约 '
            f'{deviation*100:.0f}%。可尝试提高热量适配的关注权重，保持其他未充分观察的维度不变。'
        ),
        'limitations': '这是可撤回的规则建议，不证明修改权重就能改善减脂效果，也不修改热量目标。',
        'evidence_refs': [evidence(event) for day in days for event in day['events'] if event['kind'] == 'meal'],
        'data_version': repo.version(),
        'preferences_version': repo.get_meta('preferences_version', 0),
        'rule_version': 'budget-focus-v1',
    }
