from uuid import uuid4
import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def model_settings(tmp_path):
    return Settings(data_dir=tmp_path, model_base_url='https://test.invalid/v1', model_name='test-model', model_api_key='test-only-key', scheduler_enabled=False)


def fake_client(monkeypatch, handler):
    original = httpx.Client
    def patched(*args, **kwargs):
        return original(*args, **kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr('app.parser.httpx.Client', patched)


def test_model_schema_is_checked_and_secrets_are_not_exposed(model_settings, monkeypatch):
    def handler(request):
        assert request.url.path == '/v1/chat/completions'
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"intent":"record","events":[{"kind":"weight","occurred_at":"2026-10-08T07:00:00+08:00","raw_text":"体重65公斤","value":65}]}'}}]})
    fake_client(monkeypatch, handler)
    with TestClient(create_app(model_settings)) as client:
        result = client.post('/api/chat', json={'request_id': str(uuid4()), 'text': '体重65公斤'})
        assert result.status_code == 200, result.text
        assert client.get('/api/events').json()[0]['data']['value'] == 65
        assert client.get('/api/health').json()['mode'] == 'model'
        assert 'test-only-key' not in client.get('/api/data/export').text


def test_model_failure_never_falls_back_or_writes_fake_records(model_settings, monkeypatch):
    fake_client(monkeypatch, lambda request: httpx.Response(200, json={'choices': [{'message': {'content': '{"intent":"record","events":[{"kind":"weight","value":9999}]}'}}]}))
    with TestClient(create_app(model_settings)) as client:
        response = client.post('/api/chat', json={'request_id': str(uuid4()), 'text': '体重65公斤'})
        assert response.status_code == 409
        assert '没有自动切换' in response.json()['detail']
        assert client.get('/api/events').json() == []


def test_model_cannot_modify_unknown_record_id(model_settings, monkeypatch):
    fake_client(monkeypatch, lambda request: httpx.Response(200, json={'choices': [{'message': {'content': '{"intent":"correct","correction_id":"unknown","events":[]}'}}]}))
    with TestClient(create_app(model_settings)) as client:
        response = client.post('/api/chat', json={'request_id': str(uuid4()), 'text': '修改体重'})
        assert response.status_code == 409
        assert client.get('/api/events').json() == []
