from concurrent.futures import ThreadPoolExecutor
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from threading import Event
import time

from app.common.config import Settings
from app.workflows.engine import Engine

ROOT = Path(__file__).resolve().parents[2]


def child(source, directory):
    env = {**os.environ, 'PYTHONPATH': str(ROOT / 'backend')}
    return subprocess.run([sys.executable, '-c', source, str(directory)],
                          env=env, cwd=ROOT, capture_output=True, text=True, timeout=15)


def test_stuck_execution_is_really_killed_by_watchdog(tmp_path):
    result = child('''
from pathlib import Path
from threading import Thread
import sys,time
from app.common.config import Settings
from app.common.execution_budget import RuntimeLimiter
runtime=RuntimeLimiter(Settings(execution_seconds=0.02,execution_grace_seconds=0.02),
                       status_path=Path(sys.argv[1])/'runtime.json')
Thread(target=runtime.watch,daemon=True).start()
with runtime.execution():
    time.sleep(10)
''', tmp_path)
    assert result.returncode == 70


def test_forced_exit_rolls_back_uncommitted_work_in_both_databases(tmp_path):
    result = child('''
from pathlib import Path
import os,sys
from app.common.config import Settings
from app.workflows.engine import Engine
engine=Engine(Settings(data_dir=Path(sys.argv[1]),scheduler_enabled=False), user_id='00000000-0000-4000-8000-000000000001')
for conn in (engine.repo.conn,engine.cp_conn):
    conn.execute('CREATE TABLE recovery_probe(value INTEGER)')
    conn.execute('INSERT INTO recovery_probe VALUES (1)')
    conn.commit()
    conn.execute('INSERT INTO recovery_probe VALUES (2)')
os._exit(70)
''', tmp_path)
    assert result.returncode == 70
    engine = Engine(Settings(data_dir=tmp_path, scheduler_enabled=False), user_id='00000000-0000-4000-8000-000000000001')
    try:
        for conn in (engine.repo.conn, engine.cp_conn):
            assert [row[0] for row in conn.execute('SELECT value FROM recovery_probe')] == [1]
            assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    finally:
        engine.close()


def test_completed_workflow_replay_does_not_need_capacity(tmp_path):
    engine = Engine(Settings(data_dir=tmp_path, local_only=True, scheduler_enabled=False, runtime_max_executions=1), user_id='00000000-0000-4000-8000-000000000001')
    entered, release = Event(), Event()
    payload = {'text': '今天体重65公斤', 'processing_mode': 'local_only'}
    initial = {'input_text': payload['text'], 'processing_mode': 'local_only'}
    result = engine.run('chat', 'runtime-replay-1', payload, initial)

    def occupied():
        with engine.runtime.execution():
            entered.set()
            release.wait(2)

    try:
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(occupied)
            assert entered.wait(1)
            try:
                assert engine.run('chat', 'runtime-replay-1', payload, initial) == result
                engine.delete_event(result['saved_record_ids'][0])
                assert engine.repo.event(result['saved_record_ids'][0]) is None
            finally:
                release.set()
            future.result(timeout=2)
    finally:
        engine.close()


def test_independent_healthcheck_rejects_stale_and_overdue_status():
    spec = importlib.util.spec_from_file_location('runtime_health', ROOT / 'scripts/runtime-healthcheck.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.healthy({'ready': True, 'heartbeat': 100, 'deadlines': [110]}, 101)
    assert not module.healthy({'ready': True, 'heartbeat': 100, 'deadlines': []}, 106)
    assert not module.healthy({'ready': True, 'heartbeat': 100, 'deadlines': [80]}, 101)
    assert not module.healthy({'ready': True, 'heartbeat': float('nan'), 'deadlines': []}, 101)
