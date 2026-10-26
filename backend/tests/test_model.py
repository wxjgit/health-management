from uuid import uuid4
import json
import httpx
import pytest
from fastapi.testclient import TestClient

from app.common.config import Settings
from app.main import create_app
from model_helpers import planned_content, safety_transport


@pytest.fixture
def model_settings(tmp_path):
    return Settings(data_dir=tmp_path, model_base_url='https://test.invalid/v1', model_name='test-model', model_api_key='test-only-key', scheduler_enabled=False)


def fake_client(monkeypatch, handler):
    original = httpx.Client
    def wire_handler(request):
        response = handler(request)
        data = json.loads(json.loads(request.content)['messages'][1]['content'])
        content = json.loads(response.json()['choices'][0]['message']['content'])
        # Keep the forged-ID test malicious; the new plan schema must reject it.
        if content.get('correction_id'):
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({
                'actions': [{'intent': 'correct', 'text': data['text'], 'correction_id': content['correction_id']}]
            })}}]})
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(planned_content(data, content))}}]})
    def patched(*args, **kwargs):
        return original(*args, **kwargs, transport=httpx.MockTransport(safety_transport(wire_handler)))
    monkeypatch.setattr('app.modeling.parser.httpx.Client', patched)


def test_model_schema_is_checked_and_secrets_are_not_exposed(model_settings, monkeypatch):
    def handler(request):
        assert request.url.path == '/v1/chat/completions'
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"intent":"record","events":[{"kind":"weight","occurred_at":"2026-10-08T07:00:00+08:00","raw_text":"体重65公斤","value":65}]}'}}]})
    fake_client(monkeypatch, handler)
    with TestClient(create_app(model_settings)) as client:
        result = client.post('/api/chat', json={'request_id': str(uuid4()), 'text': '体重65公斤'})
        assert result.status_code == 200, result.text
        assert result.json()['duration_ms'] >= 0
        assert [step['source'] for step in result.json()['process_trace']] == ['local', 'model', 'model', 'local', 'local', 'local']
        assert result.json()['process_trace'][2]['title'] == '理解输入'
        assert client.get('/api/events').json()[0]['data']['value'] == 65
        assert client.get('/api/health').json()['mode'] == 'model'
        assert client.get('/api/messages').json()[-1]['process_trace'] == result.json()['process_trace']
        assert client.get('/api/messages').json()[-1]['duration_ms'] == result.json()['duration_ms']
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
