from app.common.config import MODEL_API_KEY, MODEL_BASE_URL, MODEL_NAME, Settings


MODEL_VARIABLES = ('MODEL_BASE_URL', 'MODEL_API_KEY', 'MODEL_NAME')


def test_missing_model_environment_uses_builtin_model_config(tmp_path, monkeypatch):
    for name in MODEL_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('DATA_DIR', str(tmp_path))

    settings = Settings.from_env()

    assert settings.mode == 'model'
    assert settings.model_base_url == MODEL_BASE_URL
    assert settings.model_api_key == MODEL_API_KEY
    assert settings.model_name == MODEL_NAME
    assert settings.data_dir == tmp_path


def test_empty_data_directory_uses_project_default(monkeypatch):
    monkeypatch.setenv('DATA_DIR', '')

    settings = Settings.from_env()

    assert settings.data_dir.name == 'data'


def test_environment_does_not_override_builtin_model_config(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setenv('MODEL_BASE_URL', 'https://model.test.invalid/v1')
    monkeypatch.setenv('MODEL_API_KEY', 'test-only-key')
    monkeypatch.setenv('MODEL_NAME', 'test-model')

    settings = Settings.from_env()

    assert settings.mode == 'model'
    assert settings.model_base_url == MODEL_BASE_URL
    assert settings.model_api_key == MODEL_API_KEY
    assert settings.model_name == MODEL_NAME
