import asyncio
import re
import time

import pytest
from fastapi.testclient import TestClient

from app.api import auth
from app.common.config import ConfigurationError, Settings
from app.main import create_app

REAL_EXCHANGE = auth.exchange_code


@pytest.mark.parametrize('app_id,secret', [('', ''), ('app', ''), ('', 'secret')])
def test_service_refuses_missing_wechat_configuration_before_creating_data(tmp_path, app_id, secret):
    directory = tmp_path / 'not-created'
    app = create_app(Settings(data_dir=directory, wechat_app_id=app_id, wechat_app_secret=secret))
    with pytest.raises(ConfigurationError, match='WECHAT_CONFIGURATION'):
        with TestClient(app):
            pytest.fail('service started without mandatory login configuration')
    assert not directory.exists()


def test_retired_auth_environment_flag_cannot_disable_login(tmp_path, monkeypatch):
    monkeypatch.setattr('app.common.config.load_dotenv', lambda *args: None)
    for key in ('MODEL_API_KEY', 'MODEL_API_KEY_FILE', 'MODEL_BASE_URL', 'MODEL_NAME'):
        monkeypatch.delenv(key, raising=False)
    secret = tmp_path / 'wechat-secret'
    secret.write_text('synthetic-secret')
    monkeypatch.setenv('AUTH_ENABLED', 'false')
    monkeypatch.setenv('WECHAT_APP_ID', 'synthetic-app')
    monkeypatch.setenv('WECHAT_APP_SECRET_FILE', str(secret))
    monkeypatch.setenv('DATA_DIR', str(tmp_path / 'service-data'))
    settings = Settings.from_env()
    assert not hasattr(settings, 'auth_enabled')
    with TestClient(create_app(settings)) as client:
        assert client.get('/api/profile').status_code == 401
        assert client.get('/api/data/export').status_code == 401
        assert client.get('/api/auth/status').json() == {'enabled': True, 'business_ready': True}


@pytest.fixture
def client(tmp_path, monkeypatch):
    async def exchange(settings, code):
        return 'openid-b' if code == 'account-b' else 'openid-a'
    monkeypatch.setattr(auth, 'exchange_code', exchange)
    app = create_app(Settings(data_dir=tmp_path,                               wechat_app_id='test-app', wechat_app_secret='server-secret',
                              privacy_operator='Synthetic operator', privacy_contact='test@example.invalid',
                              local_only=True, scheduler_enabled=False, job_runner_enabled=False))
    with TestClient(app) as client:
        yield client


def login_body(client, code):
    return {'code': code, 'privacy_version': client.get('/api/policies').json()['version'], 'privacy_consent': True, 'health_consent': True, 'model_consent': True}


def login(client, code='account-a'):
    response = client.post('/api/auth/wechat/login', json=login_body(client, code))
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    result = response.json()
    return result, {'Authorization': 'Bearer ' + result['access_token']}


def test_identities_sessions_replay_and_logout(client):
    first, headers = login(client)
    again, another = login(client, 'new-code-same-account')
    other, _ = login(client, 'account-b')
    assert first['user'] == again['user'] != other['user']
    assert first['access_token'] != again['access_token']
    assert client.get('/api/auth/me', headers=headers).json() == {'user': first['user']}
    assert client.post('/api/auth/wechat/login', json=login_body(client, 'account-a')).status_code == 401
    assert client.post('/api/auth/logout', headers=headers).status_code == 200
    assert client.get('/api/auth/me', headers=headers).status_code == 401
    assert client.get('/api/auth/me', headers=another).status_code == 200
    with client.app.state.auth.connect() as conn:
        rows = conn.execute('SELECT * FROM sessions').fetchall()
        assert all(row['token_hash'] != first['access_token'] for row in rows)
        conn.execute('UPDATE sessions SET expires_at=0')
    assert client.get('/api/auth/me', headers=another).status_code == 401


def test_all_business_routes_require_login(client):
    _, headers = login(client)
    checked = 0
    for path, operations in client.app.openapi()['paths'].items():
        if not path.startswith('/api/') or path in ('/api/health', '/api/policies') or path.startswith('/api/auth/'):
            continue
        path = re.sub(r'\{[^}]+\}', 'test-id', path)
        for method in operations:
            if method.upper() not in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE'):
                continue
            assert client.request(method, path).status_code == 401, (method, path)
            checked += 1
    assert checked > 20
    assert client.get('/api/profile', headers=headers).status_code == 200
    assert client.get('/api/health').status_code == 200
    assert client.get('/api/auth/me', headers={'Authorization': 'Bearer forged', 'X-User-ID': 'other'}).status_code == 401


def test_failure_never_leaks_provider_secrets_or_creates_user(client, monkeypatch):
    async def failed(settings, code):
        raise auth.AuthError(502, 'wechat_unavailable', '微信登录暂时不可用，请重新登录。')
    monkeypatch.setattr(auth, 'exchange_code', failed)
    response = client.post('/api/auth/wechat/login', json=login_body(client, 'failed-code'))
    assert response.status_code == 502
    assert 'server-secret' not in response.text
    assert client.post('/api/auth/wechat/login', json={'code': 'x', 'user_id': 'forged'}).status_code == 422
    with client.app.state.auth.connect() as conn:
        assert conn.execute('SELECT count(*) FROM users').fetchone()[0] == 0


def test_persistence_rate_limit_and_configuration(client):
    session, _ = login(client)
    reopened = auth.AuthStore(client.app.state.settings)
    assert reopened.identify(session['access_token']) == session['user']
    client.app.state.auth.login_times.extend([time.monotonic()] * client.app.state.settings.login_global_per_minute)
    assert client.post('/api/auth/wechat/login', json=login_body(client, 'limited')).status_code == 429
    with pytest.raises(ConfigurationError):
        Settings().validate_wechat()


def test_real_transport_contract(client, monkeypatch, caplog):
    caplog.set_level(20, logger="httpx")
    async def handler(request):
        assert request.url.host == 'api.weixin.qq.com'
        assert request.url.params['grant_type'] == 'authorization_code'
        return auth.httpx.Response(200, json={'openid': 'verified', 'session_key': 'never-return-this'})
    original = auth.httpx.AsyncClient
    def transport_client(**kwargs):
        return original(**kwargs, transport=auth.httpx.MockTransport(handler))
    monkeypatch.setattr(auth.httpx, 'AsyncClient', transport_client)
    assert asyncio.run(REAL_EXCHANGE(client.app.state.settings, 'code')) == 'verified'
    assert 'server-secret' not in caplog.text
    assert 'jscode2session' not in caplog.text


def test_auth_environment_uses_secret_file(tmp_path, monkeypatch):
    monkeypatch.setattr('app.common.config.load_dotenv', lambda *args: None)
    for key in ('MODEL_API_KEY', 'MODEL_API_KEY_FILE', 'MODEL_BASE_URL', 'MODEL_NAME'):
        monkeypatch.delenv(key, raising=False)
    secret = tmp_path / 'wechat-secret'
    secret.write_text('synthetic-wechat-secret')
    monkeypatch.setenv('WECHAT_APP_ID', 'test-app')
    monkeypatch.setenv('WECHAT_APP_SECRET_FILE', str(secret))
    monkeypatch.setenv('AUTH_SESSION_SECONDS', '3600')
    settings = Settings.from_env()
    assert settings.auth_session_seconds == 3600
    assert settings.wechat_app_secret == 'synthetic-wechat-secret'
    assert 'synthetic-wechat-secret' not in repr(settings)
    secret.write_text('x' * 4097)
    with pytest.raises(ConfigurationError):
        Settings.from_env()
