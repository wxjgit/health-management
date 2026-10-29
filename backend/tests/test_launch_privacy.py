from concurrent.futures import ThreadPoolExecutor

import pytest

from app.api.policies import policy
from app.common.errors import RuntimeLimitError
from app.infrastructure.tenants import tenant_path
from test_auth import client, login, login_body


def test_missing_or_stale_consent_cannot_create_account(client):
    assert client.post('/api/auth/wechat/login', json={'code': 'no-consent'}).status_code == 422
    body = login_body(client, 'stale-consent')
    body['privacy_version'] = '0' * 64
    assert client.post('/api/auth/wechat/login', json=body).status_code == 428
    body['health_consent'] = False
    assert client.post('/api/auth/wechat/login', json=body).status_code == 422
    with client.app.state.auth.connect() as conn:
        assert conn.execute('SELECT count(*) FROM users').fetchone()[0] == 0


def test_model_authorization_is_optional_persistent_revocable_and_versioned(client):
    session, headers = login(client)
    store = client.app.state.auth
    uid = session['user']['id']
    assert client.get('/api/profile', headers=headers).status_code == 200
    with pytest.raises(RuntimeLimitError):
        store.require_model(uid)
    settings = client.app.state.settings
    settings.local_only = False
    settings.model_base_url = 'https://synthetic-model.invalid/v1'
    settings.model_name = 'synthetic-model'
    settings.model_api_key = 'synthetic-secret'
    assert client.get('/api/profile', headers=headers).status_code == 428
    body = {'privacy_version': policy(settings)['version'], 'privacy_consent': True, 'health_consent': True, 'model_consent': True}
    assert client.put('/api/auth/consent', headers=headers, json=body).status_code == 200
    store.require_model(uid)
    assert client.put('/api/auth/consent', headers=headers, json={**body, 'model_consent': False}).status_code == 200
    with pytest.raises(RuntimeLimitError):
        store.require_model(uid)
    assert client.get('/api/profile', headers=headers).status_code == 200
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) FROM consent_events WHERE user_id=?', (uid,)).fetchone()[0] >= 3


def test_account_closure_waits_for_active_work_and_clears_only_owner(client):
    first, a = login(client)
    second, b = login(client, 'account-b')
    host, store = client.app.state.engine, client.app.state.auth
    uid = first['user']['id']
    with host.lease(uid) as engine:
        engine.repo.set_meta('private-test', 'A')
        response = client.request('DELETE', '/api/auth/account', headers=a, json={'confirmation': '注销账号'})
        assert response.json()['state'] == 'pending'
        assert not engine._closed
        assert client.get('/api/auth/me', headers=a).status_code == 401
        with pytest.raises(Exception) as error:
            store.issue('openid-a')
        assert getattr(error.value, 'code', '') == 'account_deleting'
        engine.repo.set_meta('last-active-write', True)
    host.purge(uid)
    assert not tenant_path(host.settings.data_dir, uid).exists()
    assert uid in store.deletions_path.read_text()
    with store.connect() as conn:
        assert not conn.execute('SELECT 1 FROM users WHERE id=?', (uid,)).fetchone()
        assert not conn.execute('SELECT 1 FROM user_consents WHERE user_id=?', (uid,)).fetchone()
    assert client.get('/api/auth/me', headers=b).status_code == 200
    assert client.request('DELETE', '/api/auth/account', headers=b, json={'confirmation': 'not-confirmed'}).status_code == 422
    assert store.issue('openid-a')['user']['id'] != uid


def test_login_limit_does_not_group_unrelated_clients(client):
    store = client.app.state.auth
    store.settings.login_client_per_minute = 1
    store.limit_login('one')
    with pytest.raises(Exception):
        store.limit_login('one')
    store.limit_login('two')


def test_api_rejects_external_model_without_separate_authorization(client, monkeypatch):
    settings = client.app.state.settings
    settings.local_only = False
    settings.model_base_url = 'https://synthetic-model.invalid/v1'
    settings.model_name = 'synthetic'
    settings.model_api_key = 'synthetic-only'
    called = []
    async def transport(*args, **kwargs):
        called.append(True)
        raise AssertionError('unauthorized outbound request')
    monkeypatch.setattr('app.modeling.transport._fetch_model', transport)
    body = {**login_body(client, 'no-model-grant'), 'model_consent': False}
    session = client.post('/api/auth/wechat/login', json=body).json()
    headers = {'Authorization': 'Bearer ' + session['access_token']}
    denied = client.post('/api/chat', headers=headers, json={
        'request_id': 'without-model-consent', 'text': '今天体重65公斤', 'processing_mode': 'model_allowed',
    })
    assert denied.status_code == 403, denied.text
    assert denied.json()['code'] == 'model_consent_required'
    assert called == []
    local = client.post('/api/chat', headers=headers, json={
        'request_id': 'local-without-model-consent', 'text': '今天体重65公斤', 'processing_mode': 'local_only',
    })
    assert local.status_code == 200, local.text
    assert called == []
