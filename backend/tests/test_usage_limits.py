import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier

import pytest

from app.common.config import Settings
from app.common.errors import RuntimeLimitError
from app.common.usage import UsageController
from app.modeling import transport
from test_auth import client, login
from test_workflow_concurrency import engine, settings, model, chat


def test_atomic_global_quota_across_users_and_restart(tmp_path):
    settings = Settings(data_dir=tmp_path, model_max_concurrent=12,
                        global_model_calls_per_day=3)
    control = UsageController(settings)
    barrier = Barrier(12)

    def attempt(number):
        with control.scope('user:' + str(number)):
            barrier.wait(timeout=5)
            try:
                with control.model_attempt():
                    return True
            except RuntimeLimitError as error:
                assert error.code == 'daily_model_quota_exceeded'
                return False

    with ThreadPoolExecutor(max_workers=12) as pool:
        assert sum(pool.map(attempt, range(12))) == 3
    reopened = UsageController(settings)
    with reopened.scope('user:new'), pytest.raises(RuntimeLimitError, match='今日'):
        with reopened.model_attempt():
            pytest.fail('global daily quota bypassed')


def test_user_quota_rollover_and_failed_attempts(tmp_path, monkeypatch):
    import app.common.usage as module
    monkeypatch.setattr(module, 'now', lambda: datetime.fromisoformat('2026-10-29T23:59:59+08:00'))
    control = UsageController(Settings(data_dir=tmp_path, user_model_calls_per_day=1))
    with control.scope('user:a'):
        with pytest.raises(ValueError):
            with control.model_attempt():
                raise ValueError('simulated upstream failure')
        assert control.snapshot()['remaining'] == 0
        with pytest.raises(RuntimeLimitError):
            with control.model_attempt():
                pass
    with control.scope('user:b'), control.model_attempt():
        pass
    monkeypatch.setattr(module, 'now', lambda: datetime.fromisoformat('2026-10-30T00:00:01+08:00'))
    with control.scope('user:a'), control.model_attempt():
        assert control.snapshot()['model_attempts'] == 1
    # A late previous-day reservation must not erase the new day's count.
    with control.scope('user:c'):
        control.reserve('model', '2026-10-29', 1, 100, 'daily_model_quota_exceeded')
    with control.scope('user:a'):
        assert control.snapshot()['model_attempts'] == 1


def test_busy_slot_is_not_charged_and_is_released(tmp_path):
    control = UsageController(Settings(data_dir=tmp_path, model_max_concurrent=1))
    with control.scope('user:a'):
        with control.model_attempt():
            with pytest.raises(RuntimeLimitError) as error:
                with control.model_attempt():
                    pass
            assert error.value.code == 'model_concurrency_limited'
            assert control.snapshot()['model_attempts'] == 1
        with control.model_attempt():
            pass
        assert control.snapshot()['model_attempts'] == 2


def test_transport_caps_output_and_cancellation_does_not_leak_slots(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, model_max_concurrent=1, model_max_output_tokens=123)
    control = UsageController(settings)
    bodies = []

    async def cancelled(settings, body, purpose, observation):
        bodies.append(body)
        raise asyncio.CancelledError()

    monkeypatch.setattr(transport, '_fetch_model', cancelled)
    with control.scope('user:a'):
        for _ in range(2):
            with pytest.raises(asyncio.CancelledError):
                asyncio.run(transport.fetch_model(settings, {'max_tokens': 99999}, 'processing_plan'))
        assert control.snapshot()['model_attempts'] == 2
    assert all(body['max_tokens'] == 123 for body in bodies)


def test_authenticated_rate_limit_uses_verified_identity(client):
    _, a = login(client, 'account-a')
    _, b = login(client, 'account-b')
    client.app.state.settings.user_requests_per_minute = 1
    assert client.get('/api/auth/me', headers=a).status_code == 200
    limited = client.get('/api/auth/me', headers={**a, 'X-User-ID': 'someone-else'})
    assert limited.status_code == 429
    assert limited.json()['code'] == 'request_rate_limited'
    assert limited.headers['Retry-After'] == '60'
    assert client.get('/api/auth/me', headers=b).status_code == 200
    assert client.post('/api/auth/logout', headers=a).status_code == 200
    assert client.get('/api/health').status_code == 200


def test_authenticated_usage_never_exposes_other_accounts(client):
    first, a = login(client, 'account-a')
    _, b = login(client, 'account-b')
    with client.app.state.engine.usage.scope('user:' + first['user']['id']):
        with client.app.state.engine.usage.model_attempt():
            pass
    assert client.get('/api/auth/usage', headers=a).json()['model_attempts'] == 1
    assert client.get('/api/auth/usage', headers=b).json()['model_attempts'] == 0


def test_replayed_workflow_does_not_charge_or_write_twice(engine):
    result = chat(engine, request_id='same-request')
    with engine.usage.scope('user:' + engine.user_id):
        count = engine.usage.snapshot()['model_attempts']
    assert count > 0
    assert chat(engine, request_id='same-request') == result
    with engine.usage.scope('user:' + engine.user_id):
        assert engine.usage.snapshot()['model_attempts'] == count
    assert len(engine.repo.events()) == 1


def test_model_io_refuses_open_transaction(engine):
    from app.workflows.concurrency import _node_engine
    token = _node_engine.set(engine)
    try:
        with engine.lock:
            engine.repo.conn.execute('BEGIN IMMEDIATE')
            with pytest.raises(RuntimeError, match='transaction'):
                with engine.model_io('processing_plan'):
                    pytest.fail('database transaction held over network')
            engine.repo.conn.rollback()
    finally:
        _node_engine.reset(token)


def test_auth_mode_requires_identity_for_background_work(tmp_path):
    control = UsageController(Settings(data_dir=tmp_path,                                        wechat_app_id='app', wechat_app_secret='secret'))
    with control.scope(), pytest.raises(RuntimeLimitError) as error:
        with control.model_attempt():
            pass
    assert error.value.code == 'usage_identity_required'


def test_request_global_limit_and_minute_reset(tmp_path, monkeypatch):
    import app.common.usage as module
    instant = [12000]
    monkeypatch.setattr(module.time, 'time', lambda: instant[0])
    control = UsageController(Settings(data_dir=tmp_path, global_requests_per_minute=1))
    with control.scope('user:a'):
        control.request()
    with control.scope('user:b'), pytest.raises(RuntimeLimitError) as error:
        control.request()
    assert error.value.code == 'request_rate_limited'
    instant[0] += 60
    with control.scope('user:b'):
        control.request()


def test_quota_configuration_from_environment(monkeypatch):
    monkeypatch.setattr('app.common.config.load_dotenv', lambda *args: None)
    for key in ('MODEL_API_KEY', 'MODEL_API_KEY_FILE', 'MODEL_BASE_URL', 'MODEL_NAME'):
        monkeypatch.delenv(key, raising=False)
    for name in ('user_requests_per_minute', 'global_requests_per_minute',
                 'user_model_calls_per_day', 'global_model_calls_per_day',
                 'model_max_output_tokens', 'model_max_concurrent'):
        monkeypatch.setenv(name.upper(), '3')
    settings = Settings.from_env()
    assert settings.user_requests_per_minute == settings.global_requests_per_minute == 3
    assert settings.user_model_calls_per_day == settings.global_model_calls_per_day == 3
    assert settings.model_max_output_tokens == settings.model_max_concurrent == 3


@pytest.mark.parametrize('name', ['model_max_concurrent', 'model_max_output_tokens',
                                  'user_model_calls_per_day', 'global_model_calls_per_day',
                                  'user_requests_per_minute', 'global_requests_per_minute'])
def test_limit_config_rejects_disabled_or_invalid_values(name):
    from app.common.config import ConfigurationError
    with pytest.raises(ConfigurationError):
        Settings(**{name: 0})
