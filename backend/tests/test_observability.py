"""验证可观测契约、事务、隐私边界和真实工作流探针。"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

import httpx
import pytest
from authenticated_client import AuthenticatedClient as TestClient

from app.common.config import ConfigurationError, Settings
from app.common.execution_budget import BudgetLock
from app.common.request_context import log_failure, request_id
from app.infrastructure.repositories.repository import Repository
from app.infrastructure.unit_of_work import atomic
from app.main import create_app
from app.modeling.transport import request_model
from app.observability.context import bind, current, operation_id, thread_link
from app.observability.logging import FileSink
from app.observability.service import Observability
from app.workflows.engine import Engine

SECRET = 'SYNTHETIC_HEALTH_AUTH_SQL_NEVER_EXPORT'


def settings(tmp_path, **kwargs):
    return Settings(data_dir=tmp_path, local_only=True, scheduler_enabled=False,
                    job_runner_enabled=False, **kwargs)


@pytest.fixture
def observer(tmp_path):
    observer = Observability(settings(tmp_path))
    yield observer
    observer.close()


def events(directory):
    return [json.loads(line) for path in (directory/'observability').glob('*.jsonl')
            for line in path.read_text().splitlines()]


def metric(observer, name, **labels):
    return observer.metrics.registry.get_sample_value('health_'+name, labels)


@pytest.mark.parametrize('field,value', [('payload', SECRET), ('outcome', SECRET),
    ('duration_seconds', float('nan')), ('duration_seconds', -1), ('purpose', SECRET)])
def test_event_rejects_arbitrary_data(observer, tmp_path, field, value):
    fields = dict(purpose='estimate_nutrition', outcome='ok', status_class='2xx',
                  stage='none', duration_seconds=.1)
    fields[field] = value
    observer.emit('model_transport_finished', **fields)
    observer.close()
    assert not events(tmp_path)
    assert metric(observer, 'observability_dropped_total', sink='log', reason='invalid_event') == 1


def test_success_error_and_library_logs_contain_only_safe_metadata(observer, tmp_path, caplog):
    with bind(observer, operation=True):
        token = request_id.set(str(uuid4()))
        try:
            with caplog.at_level(logging.INFO):
                observer.emit('model_transport_finished', purpose='estimate_nutrition', outcome='ok',
                              status_class='2xx', stage='none', duration_seconds=.1)
                log_failure(SECRET)
                try:
                    raise ValueError(SECRET)
                except ValueError:
                    logging.getLogger('httpx').exception('request body %s', SECRET)
        finally:
            request_id.reset(token)
    observer.close()
    output = json.dumps(events(tmp_path)) + caplog.text + observer.metrics.render().decode()
    assert SECRET not in output
    assert 'unknown_error' in output
    assert 'unclassified_library_log' in caplog.text
    assert all('operation_id' in event for event in events(tmp_path))


def test_metric_labels_and_routes_are_finite(observer):
    observer.observe('model_transport', purpose=SECRET, status_class='2xx', outcome='ok')
    observer.observe('http_requests', route='/api/events/'+SECRET, method='GET', status_class='2xx', outcome='ok')
    observer.observe('model_transport', purpose='estimate_nutrition', status_class='2xx', outcome='ok', thread_id=SECRET)
    assert SECRET not in observer.metrics.render().decode()
    assert metric(observer, 'observability_dropped_total', sink='metric', reason='invalid_event') == 3


def test_nested_context_and_concurrent_context_are_isolated(observer):
    def worker():
        with bind(observer, operation=True):
            before = operation_id.get()
            time.sleep(.01)
            assert operation_id.get() == before
            return before
    with ThreadPoolExecutor(max_workers=3) as pool:
        ids = list(pool.map(lambda _: worker(), range(3)))
    assert len(set(ids)) == 3
    assert operation_id.get() is None and current.get() is None
    assert thread_link.get() is None


def test_thread_hmac_is_optional_versioned_and_not_plain_id(tmp_path):
    path = tmp_path/'independent.key'
    path.write_bytes(b'x'*32)
    cfg = settings(tmp_path, obs_thread_link_enabled=True,
                   obs_thread_link_key_file=str(path), obs_thread_link_key_version='v1')
    observer = Observability(cfg)
    try:
        with bind(observer, operation=True, thread=SECRET):
            observer.emit('model_fallback', purpose='estimate_nutrition', reason='model_error')
        assert observer.link(SECRET) == observer.link(SECRET)
        assert len(observer.link(SECRET)) == 64
        assert SECRET not in repr(cfg)
    finally:
        observer.close()
    output = json.dumps(events(tmp_path))
    assert SECRET not in output and 'v1' in output


@pytest.mark.parametrize('kwargs', [dict(obs_log_level=SECRET), dict(obs_enabled='true'),
    dict(obs_quality_export_enabled=True), dict(obs_enabled=False, obs_metrics_enabled=True),
    dict(obs_thread_link_enabled=True)])
def test_invalid_configuration_has_no_value_echo(tmp_path, kwargs):
    with pytest.raises(ConfigurationError) as error:
        settings(tmp_path, **kwargs)
    assert SECRET not in str(error.value)


def test_environment_observability_configuration(monkeypatch, tmp_path):
    monkeypatch.setattr('app.common.config.load_dotenv', lambda *args: None)
    monkeypatch.setenv('PROCESSING_MODE', 'local_only')
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setenv('OBS_METRICS_ENABLED', 'true')
    assert Settings.from_env().obs_metrics_enabled
    monkeypatch.setenv('OBS_ENABLED', SECRET)
    with pytest.raises(ConfigurationError) as error:
        Settings.from_env()
    assert SECRET not in str(error.value)


def test_logs_retained_by_date_and_total_budget(tmp_path):
    dropped = []
    sink = FileSink(tmp_path/'logs', dropped.append, lambda _: None, budget=100)
    sink.close()
    sink.directory.mkdir()
    today = datetime.now(timezone.utc)
    old = sink.directory/((today-timedelta(days=7)).date().isoformat()+'-000000.jsonl')
    old.write_text('old')
    for part in range(3):
        (sink.directory/(today.date().isoformat()+f'-{part:06d}.jsonl')).write_bytes(b'x'*40)
    sink.cleanup(today)
    assert not old.exists()
    assert sum(p.stat().st_size for p in sink.directory.iterdir()) <= 100


def test_full_queue_and_io_failure_do_not_raise(observer, monkeypatch):
    from queue import Full
    monkeypatch.setattr(observer.sink.queue, 'put_nowait', lambda _: (_ for _ in ()).throw(Full()))
    observer.emit('model_fallback', purpose='estimate_nutrition', reason='model_error')
    assert metric(observer, 'observability_dropped_total', sink='log', reason='queue_full') == 1
    monkeypatch.undo()
    monkeypatch.setattr(observer.sink, 'write', lambda _: (_ for _ in ()).throw(OSError(SECRET)))
    observer.emit('model_fallback', purpose='estimate_nutrition', reason='model_error')
    observer.close()
    assert metric(observer, 'observability_dropped_total', sink='log', reason='io_error') == 1


@pytest.mark.parametrize('response,outcome,status', [
    (httpx.Response(200, json={'ok': True}), 'ok', '2xx'),
    (httpx.Response(429, text=SECRET), 'http_error', '4xx'),
    (httpx.Response(503, text=SECRET), 'http_error', '5xx'),
    (httpx.Response(200, text=SECRET), 'response_invalid', '2xx'),
    (httpx.ReadTimeout(SECRET), 'timeout', 'none'),
    (httpx.ConnectError(SECRET), 'network_error', 'none'),
    (httpx.ProxyError(SECRET), 'proxy_error', 'none'),
])
def test_model_transport_classification_without_body_capture(observer, monkeypatch, response, outcome, status):
    original = httpx.AsyncClient
    def handler(request):
        if isinstance(response, Exception):
            raise response
        return response
    monkeypatch.setattr('app.modeling.transport.httpx.AsyncClient',
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler)))
    cfg = Settings(model_base_url='https://synthetic.invalid/v1', model_name='synthetic', model_api_key=SECRET)
    with bind(observer):
        if isinstance(response, httpx.Response) and response.status_code == 200 and outcome == 'ok':
            request_model(cfg, {'sensitive': SECRET}, 'estimate_nutrition')
        else:
            from app.common.errors import ModelError
            with pytest.raises(ModelError):
                request_model(cfg, {'sensitive': SECRET}, 'estimate_nutrition')
    assert metric(observer, 'model_transport_total', purpose='estimate_nutrition',
                  status_class=status, outcome=outcome) == 1


def draft(kind='weight'):
    return {'kind': kind, 'occurred_at': '2026-10-27T08:00:00+08:00',
            'raw_text': SECRET, 'value': 65, 'items': [] if kind != 'meal' else
            [{'name': SECRET, 'quantity': 1, 'unit': 'g', 'kcal': None, 'protein_g': 0,
              'carbs_g': None, 'fat_g': None}]}


def test_quality_commit_rollback_correction_replay_and_restart(tmp_path):
    repo = Repository(tmp_path/'synthetic.db')
    try:
        with pytest.raises(RuntimeError):
            with atomic(repo):
                repo.save_events([draft()], 'rolled-back')
                raise RuntimeError('rollback')
        assert not repo.quality_snapshot()
        event_id = repo.save_events([draft('meal')], 'initial')[0]
        repo.save_events([draft('meal')], 'initial')
        repo.save_events([draft('meal')], 'automatic', event_id)
        assert next(row for row in repo.quality_snapshot() if row['metric_key']=='record_corrected')['numerator'] == 0
        repo.save_events([draft('meal')], 'correct-1', event_id, user_correction=True)
        repo.save_events([draft('meal')], 'correct-2', event_id, user_correction=True)
        snapshot = repo.quality_snapshot()
        corrected = next(row for row in snapshot if row['metric_key']=='record_corrected')
        assert (corrected['numerator'], corrected['denominator']) == (1, 1)
        known_zero = next(row for row in snapshot if row['measure']=='protein_g')
        assert (known_zero['numerator'], known_zero['denominator']) == (0, 1)
        assert SECRET not in json.dumps(snapshot)
    finally:
        repo.close()
    repo = Repository(tmp_path/'synthetic.db')
    try:
        assert repo.quality_snapshot() == snapshot
        with repo.conn:
            repo.conn.execute('DELETE FROM events WHERE id=?', (event_id,))
        assert not repo.quality_snapshot()
    finally:
        repo.close()


def test_quality_schema_rejects_invalid_contributions(tmp_path):
    repo = Repository(tmp_path/'synthetic.db')
    try:
        with pytest.raises(sqlite3.IntegrityError), repo.conn:
            repo.quality_insert('event', 'internal', 'record_corrected', numerator=2)
        with pytest.raises(sqlite3.IntegrityError), repo.conn:
            repo.quality_insert('workflow', 'internal', 'nutrition_unknown')
    finally:
        repo.close()


def test_quality_window_and_no_late_correction_backfill(tmp_path, monkeypatch):
    from datetime import date
    repo = Repository(tmp_path/'synthetic.db')
    try:
        monkeypatch.setattr('app.infrastructure.repositories.observability.cohort_today', lambda: date(2026, 10, 1))
        event_id = repo.save_events([draft()], 'initial')[0]
        monkeypatch.setattr('app.infrastructure.repositories.observability.cohort_today', lambda: date(2026, 11, 1))
        with repo.conn:
            repo.quality_prune()
        repo.save_events([draft()], 'late', event_id, user_correction=True)
        assert not repo.quality_snapshot()
    finally:
        repo.close()


def test_nested_transactions_and_reentrant_locks_count_once(observer, tmp_path):
    repo = Repository(tmp_path/'synthetic.db')
    repo.conn.observer = observer
    try:
        lock = BudgetLock(observer=observer)
        with lock, lock:
            pass
        assert metric(observer, 'lock_wait_duration_seconds_count', role='repository') is None
        from threading import Event, Thread, Timer
        held, release = Event(), Event()
        def holder():
            with lock:
                held.set()
                release.wait(2)
        thread = Thread(target=holder)
        thread.start()
        assert held.wait(2)
        timer = Timer(.02, release.set)
        timer.start()
        try:
            with lock, lock:
                pass
        finally:
            release.set()
            thread.join(2)
            timer.join(2)
        assert metric(observer, 'lock_wait_duration_seconds_count', role='repository') == 1
        with atomic(repo):
            repo.set_meta('synthetic-counter', 1)
            repo.set_meta('synthetic-counter', 2)
        assert metric(observer, 'db_transaction_duration_seconds_count', database='business', outcome='ok') == 1
        with pytest.raises(RuntimeError):
            with atomic(repo):
                repo.set_meta('synthetic-counter', 3)
                raise RuntimeError(SECRET)
        assert repo.get_meta('synthetic-counter') == 2
        assert metric(observer, 'db_transaction_duration_seconds_count', database='business', outcome='error') == 1
    finally:
        repo.close()


def test_real_workflow_privacy_replay_and_metrics_access(tmp_path, caplog):
    app = create_app(settings(tmp_path, obs_metrics_enabled=True))
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        payload = {'request_id': str(uuid4()), 'text': '体重65公斤'}
        first = client.post('/api/chat', json=payload)
        assert first.status_code == 200
        assert client.post('/api/chat', json=payload).json() == first.json()
        assert client.get('/internal/metrics').status_code == 404
        raw = app.state.test_engine.observability.metrics.render().decode()
        assert 'health_workflow_attempts_total{kind="chat",operation="run",outcome="ok"} 1.0' in raw
        assert 'health_quality_numerator' not in raw
        assert payload['request_id'] not in raw and '体重' not in raw
        assert client.get('/api/events', params={'search': SECRET}).status_code == 200
        assert client.get('/api/workflows/'+SECRET).status_code == 404
        assert metric(app.state.test_engine.observability, 'workflow_attempts_total', kind='chat', operation='run', outcome='replayed') == 1
        assert metric(app.state.test_engine.observability, 'db_transaction_duration_seconds_count', database='checkpoint', outcome='ok') > 0
    output = json.dumps(events(tmp_path)) + caplog.text
    assert SECRET not in output and '65公斤' not in output
    assert 'model_fallback' not in output
    assert any(
        event.get('node') == 'execute_operations' and 'operation_id' in event
        for event in events(tmp_path)
    )
    assert 'schema_version' in output


def test_metrics_disabled_and_nonlocal_client_denied(tmp_path):
    for enabled in (False, True):
        app = create_app(settings(tmp_path/str(enabled), obs_metrics_enabled=enabled))
        with TestClient(app, client=('198.51.100.8', 50000)) as client:
            assert client.get('/internal/metrics').status_code == 404


def test_required_clarification_resume_and_quality_deletion(tmp_path):
    engine = Engine(settings(tmp_path), user_id='00000000-0000-4000-8000-000000000001')
    try:
        first = engine.run('chat', str(uuid4()), {}, {'input_text': '记录体重'})
        assert first['status'] == 'waiting_input'
        question = next(row for row in engine.repo.quality_snapshot() if row['metric_key']=='required_clarification')
        assert (question['numerator'], question['denominator']) == (1, 1)
        payload = {'request_id': str(uuid4()), 'interrupt_id': first['pending_input']['interrupt_id'], 'text': '65公斤'}
        completed = engine.resume(first['thread_id'], payload)
        assert engine.resume(first['thread_id'], payload) == completed
        event_id = completed['saved_record_ids'][0]
        preview = engine.deletions.preview('event', event_id)
        assert preview['counts']['quality_observations'] >= 1
        engine.deletions.delete('event', event_id, preview_id=preview['preview_id'])
        assert not engine.repo.conn.execute('SELECT 1 FROM quality_observations WHERE subject_id=?', (event_id,)).fetchone()
        assert metric(engine.observability, 'workflow_attempts_total', kind='chat', operation='run', outcome='interrupted') == 1
    finally:
        engine.close()


@pytest.mark.parametrize('content,outcome', [
    (None, 'envelope_invalid'), ('{"items":"invalid"}', 'schema_invalid'), ('{"items":[]}', 'ok')])
def test_model_contract_distinguishes_http_success(observer, monkeypatch, content, outcome):
    from app.contracts.events import NutritionEstimates
    from app.modeling.boundary import ApprovedCall
    from app.modeling.parser import Parser
    parser = Parser(Settings(model_base_url='https://synthetic.invalid/v1', model_api_key=SECRET, model_name='synthetic'))
    monkeypatch.setattr(parser.boundary, 'consume', lambda _: {})
    monkeypatch.setattr('app.modeling.parser.request_model', lambda *args:
        {'choices': [{'message': {'content': content}}]})
    with bind(observer):
        if outcome == 'ok':
            assert parser.model_request(SECRET, ApprovedCall('estimate_nutrition', '{}'), NutritionEstimates).items == []
        else:
            from app.common.errors import UpstreamModelError
            with pytest.raises(UpstreamModelError):
                parser.model_request(SECRET, ApprovedCall('estimate_nutrition', '{}'), NutritionEstimates)
    assert metric(observer, 'model_contract_total', purpose='estimate_nutrition', outcome=outcome) == 1


def test_plan_decisions_cancel_expiry_and_supersede_have_distinct_cohorts(tmp_path):
    repo = Repository(tmp_path/'synthetic.db')
    try:
        def plan(key, base=None):
            repo.save_plan(key, key, {'data_version': repo.version(), 'base_plan_id': base})
        plan('active')
        assert repo.decide_plan('active', True) == 'active'
        assert repo.decide_plan('active', True) == 'active'
        plan('rejected')
        assert repo.decide_plan('rejected', False) == 'rejected'
        plan('stale')
        assert repo.decide_plan('stale', True) == 'stale'
        plan('automatic', 'active')
        assert repo.decide_plan('automatic', True, decision_source='automatic') == 'active'
        for key, outcome in [('cancelled', 'cancelled'), ('expired', 'expired')]:
            repo.register_workflow(key, key+'-req', 'synthetic', 'plan')
            plan(key, 'automatic')
            repo.workflow_result(key, {'status': 'waiting_input'})
            if outcome == 'cancelled':
                repo.cancel_workflow(key)
            else:
                repo.expire_workflow(key)
                repo.expire_workflow(key)
        results = {(row['outcome'], row['decision_source']): row['numerator'] for row in repo.quality_snapshot()}
        assert results == {('active_decision', 'manual'): 1, ('active_decision', 'automatic'): 1,
                           ('rejected', 'manual'): 1, ('stale', 'manual'): 1,
                           ('cancelled', 'none'): 1, ('expired', 'none'): 1}
        assert repo.plan('active')['status'] == 'superseded'
    finally:
        repo.close()


def test_quality_export_is_explicit_and_deleted_series_disappears(tmp_path):
    app = create_app(settings(tmp_path, obs_metrics_enabled=True, obs_quality_export_enabled=True))
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        repo = app.state.test_engine.repo
        with app.state.test_engine.lock:
            event_id = repo.save_events([draft()], 'synthetic')[0]
        assert client.get('/internal/metrics').status_code == 404
        raw = app.state.test_engine.observability.metrics.render(repo.quality_snapshot()).decode()
        assert 'metric="record_corrected"' in raw
        assert event_id not in raw and SECRET not in raw
        with app.state.test_engine.lock, repo.conn:
            repo.conn.execute('DELETE FROM events WHERE id=?', (event_id,))
        assert 'metric="record_corrected"' not in app.state.test_engine.observability.metrics.render(repo.quality_snapshot()).decode()


def test_background_attempt_is_independent_and_payload_not_exported(tmp_path, monkeypatch):
    from app.common.clock import now
    engine = Engine(settings(tmp_path), user_id='00000000-0000-4000-8000-000000000001')
    outer = request_id.set(str(uuid4()))
    try:
        repo = engine.repo
        job_id = repo.enqueue_job('synthetic:'+SECRET, 'daily', {'period_end': str(now().date())},
                                  int(time.time()*1000)-100)
        def execute(engine, lease, reference):
            assert request_id.get() is None
            assert operation_id.get() is not None
            engine.repo.finish_scheduled_job(lease)
        monkeypatch.setattr('app.jobs.runner.execute_job', execute)
        assert engine.jobs.run_one()
        assert request_id.get() is not None
        assert metric(engine.observability, 'job_attempts_total', job_kind='daily', outcome='done') == 1
        assert metric(engine.observability, 'job_queue_wait_seconds_count', job_kind='daily') == 1
        assert SECRET not in engine.observability.metrics.render().decode()
    finally:
        request_id.reset(outer)
        engine.close()
    assert SECRET not in json.dumps(events(tmp_path))


def test_library_tracing_disabled_for_real_graph(tmp_path, monkeypatch):
    import langsmith.run_helpers
    engine = Engine(settings(tmp_path), user_id='00000000-0000-4000-8000-000000000001')
    try:
        captured = []
        original = engine.graph_for('chat').invoke
        def invoke(*args, **kwargs):
            captured.append(langsmith.run_helpers.get_tracing_context()['enabled'])
            return original(*args, **kwargs)
        monkeypatch.setattr(engine.chat_graph, 'invoke', invoke)
        engine.run('chat', str(uuid4()), {}, {'input_text': '体重65公斤'})
        assert captured == [False]
    finally:
        engine.close()


def test_disabled_observability_keeps_business_quality_and_no_output(tmp_path):
    engine = Engine(settings(tmp_path, obs_enabled=False), user_id='00000000-0000-4000-8000-000000000001')
    try:
        result = engine.run('chat', str(uuid4()), {}, {'input_text': '体重65公斤'})
        assert result['status'] == 'completed'
        assert engine.repo.quality_snapshot()
        assert not engine.observability.metrics.registry.get_sample_value('health_model_fallback_total')
    finally:
        engine.close()
    assert not events(tmp_path)


def test_gateway_blocks_internal_metrics_and_startup_disables_access_logs():
    root = Path(__file__).resolve().parents[2]
    assert 'location ^~ /internal/ { return 404; }' in (root/'deploy/gateway/nginx.conf').read_text(encoding='utf-8')
    assert '--no-access-log' in (root/'start.ps1').read_text(encoding='utf-8')
    assert '--no-access-log' in (root/'Dockerfile').read_text(encoding='utf-8')


def test_broken_runtime_export_does_not_break_business_request(tmp_path, monkeypatch):
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        observer = app.state.test_engine.observability
        def fail(*args, **kwargs):
            raise OSError(SECRET)
        monkeypatch.setattr(observer.sink, 'submit', fail)
        monkeypatch.setattr(observer.metrics, 'observe', fail)
        result = client.post('/api/chat', json={'request_id': str(uuid4()), 'text': '体重65公斤'})
        assert result.status_code == 200
        assert result.json()['saved_record_ids']
