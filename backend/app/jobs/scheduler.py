"""A single-process scheduler. Job receipts and notifications survive restarts."""
import asyncio
from datetime import timedelta
from ..common.clock import now
from ..health import analyze, recommendation, review
from ..privacy.provenance import capture


def tick(engine, reference=None):
    current = reference or now()
    weekly_key = None
    with engine.lock:
        repo = engine.repo
        if repo.conn.execute("SELECT 1 FROM deletion_jobs WHERE state!='completed' LIMIT 1").fetchone():
            engine.deletions.recover()
        prefs = repo.get_meta('preferences')
        day = current.date().isoformat()
        # Archiving is maintenance and remains enabled if reminders are disabled.
        key = f'archive:{day}'
        if repo.start_job(key, 'archive'):
            try:
                engine.archive(current.date())
                engine.prune_context(current)
                repo.finish_job(key)
            except Exception:
                repo.finish_job(key, False)
        if not repo.get_meta('profile'):
            return
        # Recompute reports for recorded days missed while the process was stopped.
        for offset in range(1, 8):
            missed = current.date() - timedelta(days=offset)
            if not repo.events(start=missed.isoformat(), end=missed.isoformat(), limit=1):
                continue
            key = f'daily:{missed}'
            if repo.start_job(key, 'daily'):
                try:
                    review(repo, 'day', missed)
                    repo.finish_job(key)
                except Exception:
                    repo.finish_job(key, False)
        if not prefs['notifications_enabled']:
            return
        minute = current.hour * 60 + current.minute
        for slot, clock in prefs['meal_times'].items():
            hour, mins = map(int, clock.split(':'))
            meal_minute = hour * 60 + mins
            if meal_minute - 30 <= minute < meal_minute:
                with capture(repo):
                    body, _ = recommendation(repo)
                    repo.notify(f'next:{day}:{slot}', '下一餐可以怎么吃', body)
            if meal_minute + 60 <= minute <= meal_minute + 180:
                recorded = any(e['data'].get('meal_slot') == slot for e in repo.events(start=day, end=day, kind='meal'))
                if not recorded:
                    repo.notify(f'missing:{day}:{slot}', '还有一餐没有记录', '有空时用一句话描述即可；没有吃这一餐可以忽略，不会被计为零摄入。', sources=[])
        if current.strftime('%H:%M') >= prefs['checkin_time']:
            repo.notify(f'checkin:{day}', '身体状态小记录', '可以补记体重、昨晚睡眠和实际运动。生理周期有变化时再记录，允许跳过。', sources=[])
        if current.strftime('%H:%M') >= prefs['summary_time']:
            key = f'daily:{day}'
            if repo.start_job(key, 'daily'):
                try:
                    result = review(repo, 'day')
                    repo.notify(key, '今日复盘已生成', result['summary'], sources=[repo.provenance_node('review', f'day:{day}')])
                    repo.finish_job(key)
                except Exception:
                    repo.finish_job(key, False)
            if current.weekday() == prefs['weekly_day']:
                key = f'weekly:{day}'
                if repo.start_job(key, 'weekly'):
                    weekly_key = key
                    epoch = engine.context_epoch
                    generation = repo.conn.execute('SELECT generation FROM job_generations WHERE key=?', (key,)).fetchone()
                    run_key = f'{key}:generation:{generation[0] if generation else 0}'

    # Graph execution must not inherit the scheduler's outer engine lock.
    if weekly_key:
        try:
            engine.run('analysis', run_key, {'period': 'week'}, {'period': 'week'})
            with engine.lock:
                workflow = repo.workflow_by_request(run_key)
                if engine.context_epoch != epoch or not workflow:
                    repo.finish_job(weekly_key, False)
                    return
                repo.notify(weekly_key, '本周复盘已生成', '查看历史规律与记录完整度。已有目标时，可以在计划页生成下周安排。',
                            sources=[repo.workflow_node(workflow['id'])])
                has_plan = bool(repo.active_plan())
            if has_plan:
                engine.run('plan', f'auto-plan:{run_key}', {'targets': None}, {'plan': {'targets': None}})
            with engine.lock:
                repo.finish_job(weekly_key)
        except Exception:
            with engine.lock:
                repo.finish_job(weekly_key, False)


async def scheduler_loop(engine):
    while True:
        try:
            await asyncio.to_thread(tick, engine)
        except Exception:
            # Keep the loop alive without logging health payloads or secrets.
            import logging
            logging.getLogger(__name__).warning('定时任务失败，将在下一轮重试')
        await asyncio.sleep(60)
