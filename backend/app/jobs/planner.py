"""Materialize due work; never generate reports or perform model I/O here."""
from datetime import datetime, time, timedelta
from ..common import clock as app_clock
from ..common.clock import SHANGHAI
from ..infrastructure.unit_of_work import atomic
from ..infrastructure.repositories.common import dump


def millis(value):
    return int(value.timestamp()*1000)


def plan_jobs(engine, reference=None):
    current = reference or app_clock.now()
    repo = engine.repo
    with engine.lock, atomic(repo):
        repo.quality_prune()
        prefs = repo.get_meta('preferences')
        model_allowed = not engine.settings.local_only and (engine.accounts is None or engine.accounts.consent(engine.user_id)['model_allowed'])
        mode = 'model_allowed' if model_allowed else 'local_only'
        day = current.date()
        start = datetime.combine(day, time(), SHANGHAI)
        midnight = start + timedelta(days=1)

        def enqueue(key, kind, target, due, expires=None, **extra):
            return repo.enqueue_job(key, kind, {'period_end': target.isoformat(), **extra}, millis(due),
                                    expires_at=millis(expires) if expires else None, processing_mode=mode,
                                    max_attempts=engine.settings.job_max_attempts)

        enqueue(f'archive:{day}', 'archive', day, start, midnight)
        if not repo.get_meta('profile'):
            return
        for offset in range(engine.settings.job_catchup_days, 0, -1):
            missed = day - timedelta(days=offset)
            if repo.events(start=str(missed), end=str(missed), limit=1):
                expires = datetime.combine(missed+timedelta(days=8), time(), SHANGHAI)
                enqueue(f'daily:{missed}', 'daily', missed, start, expires, send_notice=False)
        summary = datetime.combine(day, time.fromisoformat(prefs['summary_time']), SHANGHAI)
        daily_id = enqueue(f'daily:{day}', 'daily', day, summary, start+timedelta(days=8), send_notice=True)
        repo.conn.execute('''UPDATE scheduled_jobs SET scheduled_at=?,next_attempt_at=?
            WHERE id=? AND status='pending' AND attempts=0''', (millis(summary),millis(summary),daily_id))
        for offset in range(engine.settings.job_catchup_days, -1, -1):
            end = day - timedelta(days=offset)
            if end.weekday() == prefs['weekly_day']:
                due = datetime.combine(end, time.fromisoformat(prefs['summary_time']), SHANGHAI)
                if due <= current:
                    enqueue(f'weekly:{end}', 'weekly', end, due, due + timedelta(days=7))

        for slot, clock in prefs['meal_times'].items():
            meal = datetime.combine(day, time.fromisoformat(clock), SHANGHAI)
            _reminder(repo, prefs, f'next:{day}:{slot}', 'next_meal', day, meal-timedelta(minutes=30), meal,
                      mode, engine.settings.job_max_attempts, slot=slot, clock=clock)
            _reminder(repo, prefs, f'missing:{day}:{slot}', 'missing_meal', day, meal+timedelta(minutes=60), meal+timedelta(minutes=181),
                      mode, engine.settings.job_max_attempts, slot=slot, clock=clock)
        _reminder(repo, prefs, f'checkin:{day}', 'checkin', day,
                  datetime.combine(day, time.fromisoformat(prefs['checkin_time']), SHANGHAI), midnight,
                  mode, engine.settings.job_max_attempts, clock=prefs['checkin_time'])
        if not prefs['notifications_enabled']:
            repo.conn.execute("""UPDATE scheduled_jobs SET status='skipped',outcome_code='notifications_disabled',
                lease_token=NULL,lease_until=NULL WHERE kind IN ('next_meal','missing_meal','checkin')
                AND status='pending'""")


def _reminder(repo, prefs, key, kind, day, due, expires, mode, attempts, **extra):
    if not prefs['notifications_enabled']:
        return
    payload = {'period_end': str(day), **extra}
    job_id = repo.enqueue_job(key, kind, payload, millis(due), expires_at=millis(expires),
                              processing_mode=mode, max_attempts=attempts)
    # Pending reminders follow changed preferences; completed notices keep their identity.
    repo.conn.execute("""UPDATE scheduled_jobs SET payload=?,scheduled_at=?,next_attempt_at=?,expires_at=?,status='pending'
        WHERE id=? AND ((status='pending' AND (payload!=? OR scheduled_at!=?))
        OR (status='skipped' AND outcome_code='notifications_disabled'))""",
        (dump(payload),millis(due),millis(due),millis(expires),job_id,dump(payload),millis(due)))
