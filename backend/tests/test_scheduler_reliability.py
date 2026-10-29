"""Ownership races, interrupted transactions and durable workflow recovery."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Event
import time

import pytest

from app.common.clock import now
from app.common.config import Settings
from app.infrastructure.data_owner import DataDirectoryInUse
from app.infrastructure.repositories.repository import Repository
from app.infrastructure.unit_of_work import atomic
from app.jobs.context import LeaseLost, job_scope
from app.jobs.scheduler import tick
from app.workflows.engine import Engine

ROOT = Path(__file__).resolve().parents[2]
TARGETS = {'kcal': 1800, 'protein_g': 100, 'carbs_g': 215, 'fat_g': 60, 'exercise_minutes': 120}


@pytest.fixture
def engine(tmp_path):
    e = Engine(Settings(data_dir=tmp_path, local_only=True, scheduler_enabled=False, job_runner_enabled=False), user_id='00000000-0000-4000-8000-000000000001')
    yield e
    e.close()


def enqueue(repo, attempts=3):
    return repo.enqueue_job('daily:test', 'daily', {'period_end': str(now().date())}, 0, max_attempts=attempts)


def child(source, directory):
    return subprocess.run([sys.executable, '-u', '-c', source, str(directory)], cwd=ROOT,
        env={**os.environ, 'PYTHONPATH': str(ROOT/'backend')}, capture_output=True, text=True, timeout=25)


def weekly_setup(e):
    e.repo.update_profile({'age': 30})
    result = e.run('plan', 'initial-plan', {'targets': TARGETS},
        {'plan': {'targets': TARGETS}, 'processing_mode': 'local_only'})
    e.resume(result['thread_id'], {'request_id': 'approve-initial',
        'interrupt_id': result['pending_input']['interrupt_id'], 'approved': True})
    prefs = e.repo.get_meta('preferences')
    prefs.update(summary_time='00:00', weekly_day=now().weekday())
    e.repo.set_meta('preferences', prefs)
    return e.repo.enqueue_job(f'weekly:{now().date()}', 'weekly', {'period_end': str(now().date())}, 0)


def test_old_completion_cannot_reopen_done(engine):
    repo = engine.repo
    job = enqueue(repo)
    a = repo.claim_job('a')
    other = Repository(engine.settings.data_dir/'health.db')
    try:
        assert other.claim_job('b') is None
        with pytest.raises(LeaseLost):
            other.finish_scheduled_job(replace(a, token='wrong'), 'failed')
        repo.finish_scheduled_job(a)
        with pytest.raises(LeaseLost):
            other.finish_scheduled_job(a, 'failed', retry=True)
        assert repo.scheduled_job(job)['status'] == 'done'
        assert repo.scheduled_job(job)['attempts'] == 1
    finally:
        other.close()


def test_two_real_processes_race_for_one_task(engine):
    enqueue(engine.repo)
    source = """
from pathlib import Path
import sys,time,json
from app.infrastructure.repositories.repository import Repository
r=Repository(Path(sys.argv[1])/'health.db')
while not Path(sys.argv[2]).exists(): time.sleep(.01)
print(json.dumps(bool(r.claim_job('child'))),flush=True)
r.close()
"""
    signal = engine.settings.data_dir/'go'
    processes = [subprocess.Popen([sys.executable, '-u', '-c', source, str(engine.settings.data_dir), str(signal)],
        cwd=ROOT, env={**os.environ, 'PYTHONPATH': str(ROOT/'backend')},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    signal.touch()
    try:
        outputs = [p.communicate(timeout=20) for p in processes]
        assert [p.returncode for p in processes] == [0, 0], outputs
        assert sorted(json.loads(out[0]) for out in outputs) == [False, True]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


def test_expired_attempt_cannot_write(engine):
    repo = engine.repo
    job = enqueue(repo)
    first = repo.claim_job('first')
    with repo.conn:
        repo.conn.execute('UPDATE scheduled_jobs SET lease_until=0 WHERE id=?', (job,))
    second = repo.claim_job('second')
    assert second.attempt == 2
    with pytest.raises(LeaseLost), job_scope(repo, first):
        repo.set_meta('stale-write', True)
    assert repo.get_meta('stale-write') is None
    repo.finish_scheduled_job(second)


def test_final_crashed_attempt_has_explicit_terminal_failure(engine):
    job = enqueue(engine.repo, attempts=1)
    engine.repo.claim_job('lost')
    with engine.repo.conn:
        engine.repo.conn.execute('UPDATE scheduled_jobs SET lease_until=0 WHERE id=?', (job,))
    engine.repo.recover_jobs()
    row = engine.repo.scheduled_job(job)
    assert (row['status'], row['error_code']) == ('failed', 'job_attempts_exhausted')
    assert engine.repo.conn.execute('SELECT finished_at FROM job_attempts').fetchone()[0]


def test_nested_repository_commits_rollback_with_outer_transaction(engine):
    with pytest.raises(RuntimeError), atomic(engine.repo):
        engine.repo.notify('rollback-notice', 'title', 'body', sources=[])
        engine.repo.set_meta('rolled-back', True)
        raise RuntimeError('inject_before_commit')
    assert not engine.repo.notifications()
    assert engine.repo.get_meta('rolled-back') is None


def test_daily_report_notice_and_completion_rollback_together(engine, monkeypatch):
    engine.repo.update_profile({'age': 30})
    job = engine.repo.enqueue_job('daily:'+str(now().date()), 'daily',
        {'period_end': str(now().date()), 'send_notice': True}, 0)
    original = engine.repo.finish_scheduled_job
    def interrupt(lease, *args, **kwargs):
        if kwargs.get('result_ref'):
            raise RuntimeError('injected_failure')
        return original(lease, *args, **kwargs)
    monkeypatch.setattr(engine.repo, 'finish_scheduled_job', interrupt)
    engine.jobs.run_one()
    assert not engine.repo.reviews()
    assert not engine.repo.notifications()
    assert engine.repo.scheduled_job(job)['status'] == 'failed'


def test_scans_are_idempotent_and_disabled_notifications_keep_reports(engine):
    engine.repo.update_profile({'age': 30})
    prefs = engine.repo.get_meta('preferences')
    prefs.update(summary_time='00:00', notifications_enabled=False, weekly_day=now().weekday())
    engine.repo.set_meta('preferences', prefs)
    tick(engine)
    before = engine.repo.conn.execute('SELECT count(*) FROM scheduled_jobs').fetchone()[0]
    tick(engine)
    assert engine.repo.conn.execute('SELECT count(*) FROM scheduled_jobs').fetchone()[0] == before
    assert not engine.repo.notifications()
    assert {'day', 'week'} <= {r['data']['period'] for r in engine.repo.reviews()}


def test_backfill_keeps_report_date_and_suppresses_daily_notice(engine):
    engine.repo.update_profile({'age': 30})
    end = now().date()-timedelta(days=1)
    engine.repo.enqueue_job(f'daily:{end}', 'daily', {'period_end': str(end), 'send_notice': True}, 0)
    engine.jobs.drain()
    assert engine.repo.reviews()[0]['data']['end'] == str(end)
    assert not engine.repo.notifications()


def test_weekly_outbox_creates_one_plan(engine):
    job = weekly_setup(engine)
    engine.jobs.drain()
    assert engine.repo.scheduled_job(job)['status'] == 'done'
    assert len(engine.repo.plans()) == 2
    assert engine.repo.conn.execute("SELECT count(*) FROM outbox_events WHERE status='consumed'").fetchone()[0] == 1
    tick(engine)
    assert len(engine.repo.plans()) == 2
    assert engine.repo.conn.execute("SELECT count(*) FROM scheduled_jobs WHERE kind='auto_plan'").fetchone()[0] == 1


def test_outbox_child_insert_and_ack_are_atomic(engine, monkeypatch):
    weekly_setup(engine)
    engine.jobs.run_one()
    event = engine.repo.claim_outbox()
    original = engine.repo.enqueue_job
    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('after_insert_before_ack')
    monkeypatch.setattr(engine.repo, 'enqueue_job', crash)
    with pytest.raises(RuntimeError):
        engine.repo.consume_outbox(*event)
    assert engine.repo.conn.execute("SELECT count(*) FROM scheduled_jobs WHERE kind='auto_plan'").fetchone()[0] == 0
    monkeypatch.setattr(engine.repo, 'enqueue_job', original)
    engine.repo.consume_outbox(*event)
    assert engine.repo.conn.execute("SELECT count(*) FROM scheduled_jobs WHERE kind='auto_plan'").fetchone()[0] == 1
    with pytest.raises(LeaseLost):
        engine.repo.consume_outbox(*event)


def test_source_invalidation_cancels_outbox_and_routing_copies(engine):
    job = weekly_setup(engine)
    engine.jobs.run_one()
    key = engine.repo.scheduled_job(job)['logical_key']
    with atomic(engine.repo):
        engine.repo.reset_scheduled_key(key)
        engine.repo.conn.execute('INSERT INTO job_generations VALUES (?,1)', (key,))
    assert engine.repo.scheduled_job(job)['status'] == 'cancelled'
    assert tuple(engine.repo.conn.execute('SELECT status,payload FROM outbox_events').fetchone()) == ('cancelled', '{}')
    engine.jobs.drain()
    assert len(engine.repo.plans()) == 1


def test_backpressure_does_not_consume_attempt(engine):
    job = enqueue(engine.repo)
    for _ in range(2):
        engine.runtime.slots.acquire()
    try:
        assert not engine.jobs.run_one()
        assert engine.repo.scheduled_job(job)['attempts'] == 0
    finally:
        for _ in range(2):
            engine.runtime.slots.release()


def test_heartbeat_uses_separate_connection_during_slow_work(tmp_path, monkeypatch):
    e = Engine(Settings(data_dir=tmp_path, scheduler_enabled=False, job_runner_enabled=False,
        job_lease_seconds=.4, job_heartbeat_seconds=.05), user_id='00000000-0000-4000-8000-000000000001')
    started, release = Event(), Event()
    job = enqueue(e.repo)
    def slow(engine, lease, reference):
        started.set()
        assert release.wait(5)
        with engine.lock:
            engine.repo.finish_scheduled_job(lease)
    monkeypatch.setattr('app.jobs.runner.execute_job', slow)
    try:
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(e.jobs.run_one)
            try:
                assert started.wait(3)
                first = e.repo.scheduled_job(job)['lease_until']
                time.sleep(.6)
                assert e.repo.scheduled_job(job)['lease_until'] > first
                assert e.repo.claim_job('other') is None
            finally:
                release.set()
            assert future.result(timeout=3)
        assert e.repo.scheduled_job(job)['status'] == 'done'
    finally:
        e.close()


def test_auto_plan_generation_tracks_reset_weekly_source(engine):
    first = weekly_setup(engine)
    engine.jobs.drain()
    key = engine.repo.scheduled_job(first)['logical_key']
    with atomic(engine.repo):
        engine.deletions._reset_job(key)
    generation = engine.repo.job_generation(key)
    second = engine.repo.enqueue_job(key, 'weekly', {'period_end': str(now().date())}, 0)
    engine.jobs.drain()
    assert first != second
    row = engine.repo.conn.execute("SELECT * FROM scheduled_jobs WHERE kind='auto_plan' AND generation=?", (generation,)).fetchone()
    assert row and row['status'] == 'done'


def test_plan_waiting_confirmation_finishes_technical_task(engine, monkeypatch):
    weekly_setup(engine)
    import app.workflows.planning as planning
    original = planning.plan_draft
    def require_confirmation(*args, **kwargs):
        result = original(*args, **kwargs)
        result['needs_confirmation'] = True
        return result
    monkeypatch.setattr(planning, 'plan_draft', require_confirmation)
    engine.jobs.drain()
    row = engine.repo.conn.execute("SELECT status,outcome_code,result_ref FROM scheduled_jobs WHERE kind='auto_plan'").fetchone()
    assert tuple(row[:2]) == ('done', 'waiting_confirmation')
    assert engine.repo.workflow(row['result_ref'])['status'] == 'waiting_input'


def test_changed_active_plan_drops_old_outbox_intent(engine):
    weekly_setup(engine)
    engine.jobs.run_one()
    with engine.repo.conn:
        engine.repo.conn.execute("UPDATE plans SET status='superseded' WHERE status='active'")
    assert engine.jobs.consume()
    assert not engine.repo.conn.execute("SELECT 1 FROM scheduled_jobs WHERE kind='auto_plan'").fetchone()


def test_local_only_survives_event_handoff_and_restart(engine):
    job = weekly_setup(engine)
    engine.jobs.run_one()
    settings = engine.settings
    engine.close()
    e = Engine(settings, user_id='00000000-0000-4000-8000-000000000001')
    try:
        e.jobs.drain()
        row = e.repo.conn.execute("SELECT processing_mode FROM scheduled_jobs WHERE kind='auto_plan'").fetchone()
        assert row[0] == 'local_only'
        assert e.repo.conn.execute('SELECT count(*) FROM model_calls').fetchone()[0] == 0
    finally:
        e.close()


def test_expired_reminder_not_sent_and_changed_pending_time_rescheduled(engine):
    engine.repo.update_profile({'age': 30})
    moment = now().replace(hour=14, minute=0)
    engine.jobs.plan(moment)
    row = engine.repo.conn.execute("SELECT id FROM scheduled_jobs WHERE logical_key=?", (f'next:{moment.date()}:lunch',)).fetchone()
    engine.jobs.drain(moment)
    assert engine.repo.scheduled_job(row[0])['status'] == 'skipped'
    dinner = engine.repo.conn.execute("SELECT * FROM scheduled_jobs WHERE logical_key=?", (f'next:{moment.date()}:dinner',)).fetchone()
    prefs = engine.repo.get_meta('preferences')
    prefs['meal_times']['dinner'] = '19:00'
    engine.repo.set_meta('preferences', prefs)
    engine.jobs.plan(moment)
    updated = engine.repo.scheduled_job(dinner['id'])
    assert updated['payload']['clock'] == '19:00'
    assert updated['scheduled_at'] > dinner['scheduled_at']


def test_delete_during_running_report_revokes_task_and_clears_event(engine):
    engine.repo.update_profile({'age': 30})
    result = engine.run('chat', 'record-before-weekly',
        {'text': '今天体重65公斤'}, {'input_text': '今天体重65公斤', 'processing_mode': 'local_only'})
    job = engine.repo.enqueue_job('weekly:'+str(now().date()), 'weekly', {'period_end': str(now().date())}, 0)
    lease = engine.repo.claim_job('in-flight')
    target = result['saved_record_ids'][0]
    preview = engine.deletions.preview('event', target)
    assert preview['counts']['scheduled_jobs'] == 1
    engine.deletions.delete('event', target, preview_id=preview['preview_id'])
    assert engine.repo.scheduled_job(job)['status'] == 'cancelled'
    assert engine.repo.scheduled_job(job)['payload'] == {}
    with pytest.raises(LeaseLost), job_scope(engine.repo, lease):
        engine.repo.notify('stale', 'title', 'body', sources=[])


def test_shutdown_fences_running_task_before_closing_databases(engine, monkeypatch):
    job = enqueue(engine.repo)
    entered, release = Event(), Event()
    def slow(e, lease, reference):
        entered.set()
        assert release.wait(5)
        e.repo.set_meta('late-write', True)
    monkeypatch.setattr('app.jobs.runner.execute_job', slow)
    with ThreadPoolExecutor(2) as pool:
        run = pool.submit(engine.jobs.run_one)
        assert entered.wait(3)
        close = pool.submit(engine.close)
        assert engine.jobs.stop.wait(2)
        release.set()
        run.result(timeout=3)
        close.result(timeout=3)
    reopened = Engine(engine.settings, user_id='00000000-0000-4000-8000-000000000001')
    try:
        assert reopened.repo.get_meta('late-write') is None
    finally:
        reopened.close()


@pytest.mark.parametrize('options', [
    {'job_lease_seconds': 1, 'job_heartbeat_seconds': 1},
    {'job_poll_seconds': 0}, {'job_max_attempts': 0}, {'job_catchup_days': 8},
    {'job_runner_enabled': 'true'}])
def test_invalid_job_settings_fail_closed(options):
    from app.common.config import ConfigurationError
    with pytest.raises(ConfigurationError):
        Settings(**options)



def test_second_engine_rejected_before_repository_open(engine, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('ownership must precede database initialization')
    monkeypatch.setattr('app.workflows.engine.Repository', forbidden)
    with pytest.raises(DataDirectoryInUse):
        Engine(engine.settings, user_id='00000000-0000-4000-8000-000000000001')


def test_directory_lock_released_by_forced_exit(tmp_path):
    result = child("""
from app.infrastructure.data_owner import DataOwner
from pathlib import Path
import os,sys
owner=DataOwner(Path(sys.argv[1]))
os._exit(70)
""", tmp_path)
    assert result.returncode == 70, result.stderr
    e = Engine(Settings(data_dir=tmp_path, scheduler_enabled=False, job_runner_enabled=False), user_id='00000000-0000-4000-8000-000000000001')
    e.close()


@pytest.mark.parametrize('attempts,status,expected', [
    (0, 'done', 'done'), (1, 'running', 'pending'), (3, 'running', 'failed'), (3, 'pending', 'failed')])
def test_legacy_import(tmp_path, attempts, status, expected):
    repo = Repository(tmp_path/'health.db')
    with repo.conn:
        repo.conn.execute('INSERT INTO jobs VALUES (?,?,?,?,?)',
            (f'daily:{now().date()}', 'daily', status, attempts, now().isoformat()))
        repo.conn.execute('PRAGMA user_version=7')
    repo.close()
    repo = Repository(tmp_path/'health.db')
    try:
        assert repo.conn.execute('SELECT status FROM scheduled_jobs').fetchone()[0] == expected
        assert repo.conn.execute('PRAGMA user_version').fetchone()[0] == 9
    finally:
        repo.close()


def test_failed_migration_rolls_back_schema_and_version(tmp_path, monkeypatch):
    import app.infrastructure.database as db
    conn = sqlite3.connect(tmp_path/'health.db')
    conn.execute('PRAGMA user_version=7')
    conn.close()
    original = db.execute_schema
    def fail(connection, script):
        original(connection, script)
        if 'CREATE TABLE IF NOT EXISTS scheduled_jobs' in script:
            raise RuntimeError('migration_interrupted')
    monkeypatch.setattr(db, 'execute_schema', fail)
    with pytest.raises(RuntimeError):
        Repository(tmp_path/'health.db')
    with sqlite3.connect(tmp_path/'health.db') as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 7
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='scheduled_jobs'").fetchone()
    monkeypatch.setattr(db, 'execute_schema', original)
    Repository(tmp_path/'health.db').close()


def test_completed_graph_recovers_after_exit_before_job_finish(tmp_path):
    result = child("""
from app.common.config import Settings
from app.common.clock import now
from app.workflows.engine import Engine
from pathlib import Path
import os,sys
e=Engine(Settings(data_dir=Path(sys.argv[1]),scheduler_enabled=False,job_runner_enabled=False,local_only=True), user_id='00000000-0000-4000-8000-000000000001')
e.repo.update_profile({'age':30})
e.repo.enqueue_job('weekly:'+str(now().date()),'weekly',{'period_end':str(now().date())},0)
original=e.repo.workflow_result
def kill(thread,result):
    original(thread,result)
    if result['status']=='completed': os._exit(70)
e.repo.workflow_result=kill
e.jobs.run_one()
""", tmp_path)
    assert result.returncode == 70, result.stderr
    with sqlite3.connect(tmp_path/'health.db') as conn:
        conn.execute('UPDATE scheduled_jobs SET lease_until=0')
    e = Engine(Settings(data_dir=tmp_path, scheduler_enabled=False, job_runner_enabled=False, local_only=True), user_id='00000000-0000-4000-8000-000000000001')
    try:
        e.jobs.drain()
        assert e.repo.conn.execute('SELECT status FROM scheduled_jobs').fetchone()[0] == 'done'
        assert len(e.repo.reviews()) == 1
        assert len(e.repo.notifications()) == 1
        assert e.repo.conn.execute('SELECT count(*) FROM workflows').fetchone()[0] == 1
    finally:
        e.close()

