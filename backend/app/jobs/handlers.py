"""Approved handlers and short, atomic completion transactions."""
from datetime import date, datetime, timedelta, time
from ..common import clock
from ..common.clock import SHANGHAI
from ..common.errors import DomainValidationError
from ..health import review, recommendation
from ..privacy.provenance import capture
from ..infrastructure.unit_of_work import atomic
from .contracts import validate_payload


def execute_job(engine, lease, reference=None):
    repo = engine.repo
    job = repo.scheduled_job(lease.id)
    if engine.accounts is not None and not engine.accounts.consent(engine.user_id)['model_allowed']:
        job['processing_mode'] = 'local_only'
    payload = job['payload']
    if job['payload_version'] != 1:
        raise ValueError('job_payload_unsupported')
    validate_payload(job['kind'], payload)
    current = reference or clock.now()
    end = date.fromisoformat(payload['period_end'])
    kind = job['kind']
    if kind == 'weekly':
        return weekly(engine, lease, job, current)
    if kind == 'auto_plan':
        return auto_plan(engine, lease, job, current)
    if kind == 'archive':
        # Deletion/compaction span two databases and manage their own recovery.
        with engine.lock:
            engine.archive(end)
            engine.prune_context(current)
            repo.prune_job_history()
            repo.finish_scheduled_job(lease)
        return
    with engine.lock, atomic(repo):
        repo.assert_lease(lease)
        if kind == 'daily':
            result = review(repo, 'day', end)
            prefs = repo.get_meta('preferences')
            if payload.get('send_notice') and end == current.date() and prefs['notifications_enabled']:
                repo.notify(job['logical_key'], '今日复盘已生成', result['summary'],
                            sources=[repo.provenance_node('review', f'day:{end}')])
            repo.finish_scheduled_job(lease, result_ref=f'day:{end}')
            return
        prefs = repo.get_meta('preferences')
        if not prefs['notifications_enabled']:
            repo.finish_scheduled_job(lease, 'skipped', outcome='notifications_disabled')
            return
        if end != current.date() or not reminder_valid(kind, payload, prefs, current):
            repo.finish_scheduled_job(lease, 'skipped', outcome='reminder_changed_or_expired')
            return
        if kind == 'next_meal':
            with capture(repo):
                body, _ = recommendation(repo, str(end))
                repo.notify(job['logical_key'], '下一餐可以怎么吃', body)
        elif kind == 'missing_meal':
            recorded = any(e['data'].get('meal_slot') == payload['slot'] for e in repo.events(start=str(end),end=str(end),kind='meal'))
            if recorded:
                repo.finish_scheduled_job(lease, 'skipped', outcome='meal_already_recorded')
                return
            repo.notify(job['logical_key'], '还有一餐没有记录', '有空时用一句话描述即可；没有吃这一餐可以忽略，不会被计为零摄入。', sources=[])
        elif kind == 'checkin':
            repo.notify(job['logical_key'], '身体状态小记录', '可以补记体重、昨晚睡眠和实际运动。生理周期有变化时再记录，允许跳过。', sources=[])
        repo.finish_scheduled_job(lease)


def reminder_valid(kind, payload, prefs, current):
    clock = payload['clock']
    if kind == 'checkin':
        return prefs['checkin_time'] == clock and current.strftime('%H:%M') >= clock
    if prefs['meal_times'].get(payload['slot']) != clock:
        return False
    minute = current.hour*60+current.minute
    meal = time.fromisoformat(clock)
    delta = minute - (meal.hour*60+meal.minute)
    return -30 <= delta < 0 if kind == 'next_meal' else 60 <= delta <= 180


def weekly(engine, lease, job, current):
    repo = engine.repo
    end = job['payload']['period_end']
    request = f"{job['logical_key']}:generation:{job['generation']}"
    initial = {'period': 'week', 'period_end': end, 'processing_mode': job['processing_mode']}
    # Keep existing legacy fingerprints where a workflow already exists.
    existing = repo.workflow_by_request(request)
    from ..workflows.runtime import fingerprint
    legacy = existing and existing['fingerprint'] == fingerprint({'kind': 'analysis', 'data': {'period': 'week'}})
    request_data = {'period': 'week'} if legacy else {'period': 'week', 'period_end': end}
    result = engine.run('analysis', request, request_data, initial)
    with engine.lock, atomic(repo):
        repo.assert_lease(lease)
        workflow = repo.workflow(result['thread_id'])
        if not workflow or workflow['status'] != 'completed':
            raise DomainValidationError()
        prefs = repo.get_meta('preferences')
        if prefs['notifications_enabled']:
            repo.notify(job['logical_key'], '本周复盘已生成', '查看历史规律与记录完整度。已有目标时，可以在计划页生成下周安排。',
                        sources=[repo.workflow_node(workflow['id'])])
        active = repo.active_plan()
        expiry = datetime.combine(date.fromisoformat(end)+timedelta(days=7), time(), SHANGHAI)
        if active and current < expiry:
            repo.stage_weekly_event(job, {'period_end': end, 'schedule_anchor': str(date.fromisoformat(end)+timedelta(days=1)),
                'source_job_id': job['id'], 'source_workflow': workflow['id'], 'base_plan_id': active['id']})
        repo.finish_scheduled_job(lease, result_ref=workflow['id'])


def auto_plan(engine, lease, job, current):
    repo = engine.repo
    payload = job['payload']
    with engine.lock:
        source = repo.scheduled_job(payload['source_job_id'])
        workflow = repo.workflow(payload['source_workflow'])
        active = repo.active_plan()
        valid = source and source['status'] == 'done' and source['generation'] == repo.job_generation(source['logical_key'])
        if not valid or not workflow:
            repo.finish_scheduled_job(lease, 'cancelled', outcome='job_source_deleted')
            return
        request = f"auto-plan:{source['logical_key']}:generation:{source['generation']}"
        existing = repo.workflow_by_request(request)
        if not existing and (not active or active['id'] != payload['base_plan_id']):
            repo.finish_scheduled_job(lease, 'skipped', outcome='base_plan_changed')
            return
    initial = {'plan': {'targets': None}, 'schedule_anchor': payload['schedule_anchor'], 'processing_mode': job['processing_mode']}
    result = engine.run('plan', request, {'targets': None}, initial)
    with engine.lock, atomic(repo):
        repo.assert_lease(lease)
        outcome = 'waiting_confirmation' if result['status'] == 'waiting_input' else 'completed'
        if result.get('safety', {}).get('category', 'general') != 'general':
            outcome = 'business_blocked'
        repo.finish_scheduled_job(lease, outcome=outcome, result_ref=result['thread_id'])
