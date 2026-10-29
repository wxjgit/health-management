from concurrent.futures import ThreadPoolExecutor
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import auth
from app.common.clock import now
from app.common.config import Settings
from app.common.errors import RuntimeLimitError
from app.infrastructure.tenants import tenant_path
from app.main import create_app
from app.workflows.engine import Engine
from test_agent import TARGETS
from test_workflow_concurrency import model


@pytest.fixture
def setup(tmp_path, monkeypatch):
    async def exchange(settings, code):
        return 'openid:' + code
    monkeypatch.setattr(auth, 'exchange_code', exchange)
    settings = Settings(data_dir=tmp_path, wechat_app_id='test',
                        wechat_app_secret='secret', privacy_operator='Synthetic operator', privacy_contact='test@example.invalid', local_only=True, tenant_cache_size=2,
                        scheduler_enabled=False, job_runner_enabled=False,
                        user_requests_per_minute=1000)
    app = create_app(settings)
    with TestClient(app) as client:
        accounts = []
        for code in ('account-a', 'account-b'):
            session = client.post('/api/auth/wechat/login', json={'code': code, 'privacy_version': client.get('/api/policies').json()['version'], 'privacy_consent': True, 'health_consent': True, 'model_consent': True}).json()
            accounts.append((session['user']['id'], {'Authorization': 'Bearer ' + session['access_token']}))
        yield client, app, settings, accounts


def chat(client, headers, text='今天体重65公斤', request_id='shared-request-id'):
    response = client.post('/api/chat', headers=headers, json={
        'text': text, 'request_id': request_id, 'conversation_id': 'shared-conversation',
        'processing_mode': 'local_only',
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_accounts_have_separate_records_profiles_messages_and_exports(setup):
    client, app, settings, ((aid, a), (bid, b)) = setup
    one = chat(client, a)
    two = chat(client, b, '今天体重78公斤')
    assert one['thread_id'] != two['thread_id']
    assert client.get('/api/events', headers=a).json()[0]['data']['value'] == 65
    assert client.get('/api/events', headers=b).json()[0]['data']['value'] == 78
    assert len(client.get('/api/events', headers=a).json()) == 1
    assert chat(client, a) == one
    for headers, other_text in ((a, '78公斤'), (b, '65公斤')):
        assert other_text not in json.dumps(client.get('/api/data/export', headers=headers).json(), ensure_ascii=False)
        assert other_text not in json.dumps(client.get('/api/messages', headers=headers).json(), ensure_ascii=False)
    profile = {'age': 30, 'sex': 'female', 'height_cm': 165, 'weight_kg': 65,
               'goal_weight_kg': 60, 'goal_date': '2027-01-01', 'activity': 'light',
               'nutrition_context': 'general'}
    assert client.put('/api/profile', headers=a, json=profile).status_code == 200
    assert client.get('/api/profile', headers=b).json() is None
    assert client.get('/api/profile', headers={**b, 'X-User-ID': aid}).json() is None
    assert not (settings.data_dir / 'health.db').exists()
    assert (tenant_path(settings.data_dir, aid) / 'health.db').is_file()
    assert (tenant_path(settings.data_dir, bid) / 'checkpoints.db').is_file()


def test_cross_account_ids_and_privacy_deletion_cannot_affect_owner(setup):
    client, app, settings, ((aid, a), (bid, b)) = setup
    result = chat(client, a)
    chat(client, b, '今天体重78公斤')
    event = client.get('/api/events', headers=a).json()[0]['id']
    thread = result['thread_id']
    assert client.get(f'/api/events/{event}/revisions', headers=b).status_code == 404
    assert client.get(f'/api/workflows/{thread}', headers=b).status_code == 404
    assert client.post(f'/api/workflows/{thread}/resume', headers=b, json={
        'request_id': 'attack-resume-id', 'interrupt_id': str(uuid4()), 'text': '80公斤',
    }).status_code in (404, 409)
    assert client.post('/api/deletions/preview', headers=b,
                       json={'target_type': 'event', 'target_id': event}).status_code == 404
    preview = client.post('/api/deletions/preview', headers=a,
                          json={'target_type': 'event', 'target_id': event}).json()
    assert client.post('/api/deletions', headers=b, json={
        'preview_id': preview['preview_id'], 'request_id': 'same-delete-id',
    }).status_code == 404
    assert len(client.get('/api/events', headers=a).json()) == 1
    deleted = client.post('/api/deletions', headers=a, json={
        'preview_id': preview['preview_id'], 'request_id': 'same-delete-id',
    })
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()['state'] == 'completed'
    assert client.get('/api/events', headers=a).json() == []
    assert client.get('/api/events', headers=b).json()[0]['data']['value'] == 78
    deletion_id = deleted.json()['deletion_id']
    assert client.get(f'/api/deletions/{deletion_id}', headers=b).status_code == 404


def test_waiting_workflow_and_plan_are_owned_and_survive_eviction(setup):
    client, app, settings, ((aid, a), (bid, b)) = setup
    settings.tenant_cache_size = 1
    pending = chat(client, a, '今天记录体重')
    assert pending['status'] == 'waiting_input', pending
    assert client.get('/api/workflows', headers=b).json() == []
    path = '/api/workflows/' + pending['thread_id'] + '/resume'
    answer = {'request_id': 'same-resume-id', 'interrupt_id': pending['pending_input']['interrupt_id'], 'text': '65公斤'}
    assert client.post(path, headers=b, json=answer).status_code in (404, 409)
    resumed = client.post(path, headers=a, json=answer)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()['status'] == 'completed'
    plan = client.post('/api/plans/propose', headers=a,
                       json={'request_id': 'shared-plan-id', 'targets': TARGETS})
    assert plan.status_code == 200, plan.text
    assert client.get('/api/plans', headers=b).json() == []
    assert client.get('/api/events', headers=a).json()[0]['data']['value'] == 65
    with app.state.engine.lease(aid) as engine:
        assert engine.runtime is app.state.engine.runtime
        assert engine.usage is app.state.engine.usage
        assert not engine.jobs.threads


def test_cache_never_evicts_active_engines_and_retains_data(setup):
    client, app, settings, ((aid, a), (bid, b)) = setup
    settings.tenant_cache_size = 1
    with app.state.engine.lease(aid) as held:
        held.repo.set_meta('marker', 'account-a')
        with pytest.raises(RuntimeLimitError):
            with app.state.engine.lease(bid):
                pass
        assert not held._closed
    with app.state.engine.lease(bid) as engine:
        assert engine.repo.get_meta('marker') is None
    assert held._closed
    with app.state.engine.lease(aid) as reopened:
        assert reopened.repo.get_meta('marker') == 'account-a'
    assert len(app.state.engine.entries) == 1


def test_restart_restores_each_account_and_session(setup):
    client, app, settings, ((aid, a), (bid, b)) = setup
    chat(client, a)
    chat(client, b, '今天体重78公斤')
    # TestClient is shut down before opening a second process-lifetime owner.
    client.__exit__(None, None, None)
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get('/api/events', headers=a).json()[0]['data']['value'] == 65
        assert restarted.get('/api/events', headers=b).json()[0]['data']['value'] == 78


def test_background_jobs_visit_correct_user_even_when_evicted(setup):
    client, app, settings, ((aid, a), (bid, b)) = setup
    settings.tenant_cache_size = 1
    for uid in (aid, bid):
        with app.state.engine.lease(uid) as engine:
            engine.repo.set_meta('profile', {'age': 30})
    settings.scheduler_enabled = True
    app.state.engine.jobs.visit(aid)
    with app.state.engine.lease(aid) as engine:
        assert engine.repo.conn.execute('SELECT count(*) FROM scheduled_jobs').fetchone()[0] > 0
        engine.repo.notify('same-key', '账号 A 提醒', '仅 A', sources=[])
    assert client.get('/api/notifications', headers=b).json() == []
    app.state.engine.jobs.visit(bid)
    with app.state.engine.lease(bid) as engine:
        assert engine.repo.conn.execute('SELECT count(*) FROM scheduled_jobs').fetchone()[0] > 0
    assert client.get('/api/notifications', headers=a).json()[0]['title'] == '账号 A 提醒'


def test_model_work_and_quotas_use_the_engine_owner(setup, model):
    client, app, settings, ((aid, a), (bid, b)) = setup
    settings.local_only = False
    settings.model_base_url = 'https://test.invalid/v1'
    settings.model_name = 'test-model'
    settings.model_api_key = 'synthetic-test-only'
    # Model mode requires an explicit model_allowed request, with verified API identity.
    for headers in (a, b):
        assert client.put('/api/auth/consent', headers=headers, json={'privacy_version': client.get('/api/policies').json()['version'], 'privacy_consent': True, 'health_consent': True, 'model_consent': True}).status_code == 200
        response = client.post('/api/chat', headers=headers, json={
            'text': '今天体重65公斤', 'request_id': 'same-model-request',
            'conversation_id': 'same-model-conversation', 'processing_mode': 'model_allowed',
        })
        assert response.status_code == 200, response.text
        assert response.json()['status'] == 'completed'
    count_a = client.get('/api/auth/usage', headers=a).json()['model_attempts']
    count_b = client.get('/api/auth/usage', headers=b).json()['model_attempts']
    assert count_a > 0 and count_a == count_b
    assert len(client.get('/api/events', headers=a).json()) == 1
    assert len(client.get('/api/events', headers=b).json()) == 1


def test_facts_notifications_and_plan_confirmation_are_isolated(setup):
    client, app, settings, ((aid, a), (bid, b)) = setup
    fact = client.post('/api/facts', headers=a, json={'content': '不喜欢燕麦'})
    assert fact.status_code == 200, fact.text
    assert client.get('/api/facts', headers=b).json() == []
    fid = fact.json()['id']
    assert client.put('/api/facts/' + fid, headers=b, json={
        'content': '不喜欢牛奶', 'expected_version': fact.json()['version'],
    }).status_code == 409
    assert client.delete('/api/facts/' + fid, headers=b).status_code == 404
    assert client.get('/api/facts', headers=a).json()[0]['content'] == '不喜欢燕麦'
    with app.state.engine.lease(aid) as engine:
        engine.repo.notify('same-notification', 'A 提醒', '仅 A', sources=[])
        nid = engine.repo.notifications()[0]['id']
    assert client.patch('/api/notifications/' + nid, headers=b).status_code == 404
    assert client.get('/api/notifications', headers=a).json()[0]['read'] == 0
    assert client.patch('/api/notifications/' + nid, headers=a).status_code == 200
    proposal = client.post('/api/plans/propose', headers=a, json={
        'request_id': 'plan-confirm-request', 'targets': TARGETS,
    }).json()
    answer = {'request_id': 'plan-approve-request',
              'interrupt_id': proposal['pending_input']['interrupt_id'], 'approved': True}
    path = '/api/workflows/' + proposal['thread_id'] + '/resume'
    assert client.post(path, headers=b, json=answer).status_code == 409
    assert client.post(path, headers=a, json=answer).status_code == 200
    assert client.get('/api/plans', headers=b).json() == []


def test_daily_report_execution_writes_only_to_its_user(setup):
    client, app, settings, ((aid, a), (bid, b)) = setup
    profile = {'age': 30, 'sex': 'female', 'height_cm': 165, 'weight_kg': 65,
               'goal_weight_kg': 60, 'goal_date': '2027-01-01', 'activity': 'light',
               'nutrition_context': 'general'}
    assert client.put('/api/profile', headers=a, json=profile).status_code == 200
    chat(client, a, '昨天体重65公斤')
    settings.scheduler_enabled = settings.job_runner_enabled = True
    # Execute directly to make task timing deterministic in this integration test.
    for _ in range(4):
        app.state.engine.jobs.visit(aid)
    assert client.get('/api/reviews', headers=a).json()
    assert client.get('/api/reviews', headers=b).json() == []
    with app.state.engine.lease(aid) as engine:
        assert engine.repo.conn.execute('SELECT count(*) FROM job_attempts').fetchone()[0] > 0
    with app.state.engine.lease(bid) as engine:
        assert engine.repo.conn.execute('SELECT count(*) FROM job_attempts').fetchone()[0] == 0


def test_parallel_requests_with_same_ids_do_not_share_results(setup):
    client, app, settings, ((aid, a), (bid, b)) = setup
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(chat, client, a, '今天体重65公斤')
        second = pool.submit(chat, client, b, '今天体重78公斤')
        assert first.result(timeout=10)['thread_id'] != second.result(timeout=10)['thread_id']
    assert client.get('/api/events', headers=a).json()[0]['data']['value'] == 65
    assert client.get('/api/events', headers=b).json()[0]['data']['value'] == 78


def test_offline_user_management_respects_root_owner(setup, monkeypatch, capsys):
    from app.jobs.manage import main
    client, app, settings, ((aid, a), (bid, b)) = setup
    chat(client, a)
    monkeypatch.setattr('sys.argv', ['manage', '--data-dir', str(settings.data_dir),
                                    '--user-id', aid, 'status'])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert 'stop the service' in capsys.readouterr().err
    client.__exit__(None, None, None)
    main()
    assert isinstance(json.loads(capsys.readouterr().out), dict)


@pytest.mark.parametrize('value', ['../escape', '/tmp/escape', 'not-a-uuid', '*'])
def test_client_values_cannot_select_a_data_directory(tmp_path, value):
    with pytest.raises(ValueError):
        tenant_path(tmp_path, value)
