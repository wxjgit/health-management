from pathlib import Path

import pytest

from app.common.config import ConfigurationError, Settings


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    import os
    for key in list(os.environ):
        if key.startswith(('MODEL_', 'RUNTIME_', 'EXECUTION_', 'REQUEST_', 'CONTEXT_')) or key in ('DATA_DIR', 'PROCESSING_MODE'):
            monkeypatch.delenv(key)
    monkeypatch.setattr('app.common.config.load_dotenv', lambda *a: None)


def test_no_builtin_credentials():
    settings = Settings.from_env()
    assert settings.mode == 'demo'
    assert not settings.model_api_key


def test_explicit_model_environment(monkeypatch, tmp_path):
    monkeypatch.setenv('MODEL_BASE_URL', 'https://model.test.invalid/v1')
    monkeypatch.setenv('MODEL_NAME', 'test-model')
    monkeypatch.setenv('MODEL_API_KEY', 'synthetic-only')
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    settings = Settings.from_env()
    assert settings.mode == 'model'
    assert settings.data_dir == tmp_path
    assert 'synthetic-only' not in repr(settings)


def test_secret_file_conflict_empty_oversize(monkeypatch, tmp_path):
    secret = tmp_path / 'secret'
    monkeypatch.setenv('MODEL_API_KEY_FILE', str(secret))
    for content in ('', 'x' * 4097, 'two\nlines'):
        secret.write_text(content)
        with pytest.raises(ConfigurationError):
            Settings.from_env()
    secret.write_text('synthetic-only\n')
    monkeypatch.setenv('PROCESSING_MODE', 'local_only')
    assert Settings.from_env().model_api_key == 'synthetic-only'
    monkeypatch.setenv('MODEL_API_KEY', 'other')
    with pytest.raises(ConfigurationError, match='SOURCE'):
        Settings.from_env()


@pytest.mark.parametrize('url', ['http://dashscope.aliyuncs.com/v1',
    'https://dashscope.aliyuncs.com.attacker.invalid/v1', 'https://127.0.0.1/v1',
    'https://user:password@dashscope.aliyuncs.com/v1', 'https://dashscope.aliyuncs.com:444/v1',
    'https://dashscope.aliyuncs.com/v1?secret=never-public'])
def test_hardened_rejects_unapproved_targets(url):
    with pytest.raises(ConfigurationError) as error:
        Settings(data_dir=Path('/data'), runtime_profile='hardened', model_base_url=url,
                 model_name='model', model_api_key='synthetic-only', model_egress_proxy='http://egress:3128')
    assert 'never-public' not in str(error.value)


def test_hardened_local_only_needs_no_model(monkeypatch):
    monkeypatch.setenv('RUNTIME_PROFILE', 'hardened')
    monkeypatch.setenv('PROCESSING_MODE', 'local_only')
    assert Settings.from_env().local_only


@pytest.mark.parametrize('name,value', [('EXECUTION_SECONDS', 'nan'), ('REQUEST_MAX_BYTES', '0'),
                                        ('RUNTIME_MAX_EXECUTIONS', '-1'), ('PROCESSING_MODE', 'invalid')])
def test_invalid_limits_fail_closed(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigurationError):
        Settings.from_env()
