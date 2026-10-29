"""Storage contention, checkpoint rollback and offline administration."""
import json
import sqlite3
import time
import pytest

from app.infrastructure.repositories.repository import Repository
from app.jobs.context import LeaseLost, job_scope
from app.workflows.engine import Engine
from test_scheduler_reliability import engine, enqueue, child


def test_busy_database_does_not_consume_claim(engine):
    other = sqlite3.connect(engine.settings.data_dir/'health.db')
    job = enqueue(engine.repo)
    other.execute('BEGIN IMMEDIATE')
    started = time.monotonic()
    try:
        with pytest.raises(sqlite3.OperationalError):
            engine.repo.claim_job('busy')
        assert time.monotonic()-started < 3
    finally:
        other.rollback()
        other.close()
    assert engine.repo.scheduled_job(job)['attempts'] == 0
    assert engine.repo.claim_job('after-release').attempt == 1


def test_expired_lease_rolls_back_checkpoint_commit(engine):
    enqueue(engine.repo)
    lease = engine.repo.claim_job('checkpoint')
    engine.cp_conn.execute('CREATE TABLE checkpoint_probe(value INTEGER)')
    engine.cp_conn.commit()
    with pytest.raises(LeaseLost), job_scope(engine.repo, lease):
        with engine.checkpointer.cursor() as cursor:
            cursor.execute('INSERT INTO checkpoint_probe VALUES (1)')
            with engine.repo.conn:
                engine.repo.conn.execute('UPDATE scheduled_jobs SET lease_until=0 WHERE id=?', (lease.id,))
    assert engine.cp_conn.execute('SELECT count(*) FROM checkpoint_probe').fetchone()[0] == 0


def test_manual_retry_is_audited_and_pause_persists(engine):
    job = enqueue(engine.repo, attempts=1)
    lease = engine.repo.claim_job('failed')
    engine.repo.finish_scheduled_job(lease, 'failed', code='job_dependency_unavailable')
    engine.repo.retry_failed_job(job, 'upstream_recovered')
    engine.repo.set_meta('jobs_paused', True)
    assert not engine.jobs.run_one()
    settings = engine.settings
    engine.close()
    restarted = Engine(settings, user_id='00000000-0000-4000-8000-000000000001')
    try:
        assert restarted.repo.get_meta('jobs_paused')
        restarted.repo.set_meta('jobs_paused', False)
        assert restarted.jobs.run_one()
        assert restarted.repo.scheduled_job(job)['status'] == 'done'
        assert restarted.repo.conn.execute('SELECT reason FROM job_admin_actions').fetchone()[0] == 'upstream_recovered'
    finally:
        restarted.close()


def test_offline_management_refuses_live_owner(tmp_path):
    from app.common.config import Settings
    from app.infrastructure.tenants import TenantHost
    host = TenantHost(Settings(data_dir=tmp_path))
    with host.lease('00000000-0000-4000-8000-000000000001'):
        pass
    source = """
from app.jobs.manage import main
import sys
sys.argv=['manage','--data-dir',sys.argv[1],'--user-id','00000000-0000-4000-8000-000000000001','status']
main()
"""
    try:
        result = child(source, tmp_path)
        assert result.returncode == 2
        assert 'data_directory_in_use' in result.stderr
    finally:
        host.close()
    result = child(source, tmp_path)
    assert result.returncode == 0, result.stderr
    assert 'failed_jobs' in json.loads(result.stdout)
    assert 'lease_token' not in result.stdout


def test_newer_schema_rejected_without_migration(tmp_path):
    with sqlite3.connect(tmp_path/'health.db') as connection:
        connection.execute('PRAGMA user_version=10')
    with pytest.raises(ValueError, match='数据库版本'):
        Repository(tmp_path/'health.db')
    with sqlite3.connect(tmp_path/'health.db') as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 10
        assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name='scheduled_jobs'").fetchone()
