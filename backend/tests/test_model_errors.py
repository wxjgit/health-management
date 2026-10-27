import json
from uuid import uuid4
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.common.config import Settings
from app.main import create_app
from app.common.errors import ModelUnavailableError
from app.chat.replies import express_plan, make_plan
from app.health.nutrition import prepare_nutrition
from app.workflows.safety import SafetyWorkflowMixin
from app.contracts.events import EventDraft


SECRET = 'synthetic-upstream-body-key-health'


@pytest.mark.parametrize(('failure', 'status', 'code'), [
    ('connect', 503, 'model_unavailable'),
    ('read_timeout', 503, 'model_unavailable'),
    ('connect_timeout', 503, 'model_unavailable'),
    ('write_timeout', 503, 'model_unavailable'),
    ('pool_timeout', 503, 'model_unavailable'),
    (429, 503, 'model_rate_limited'),
    (503, 503, 'model_service_unavailable'),
    (401, 502, 'model_upstream_failed'),
    (403, 502, 'model_upstream_failed'),
    (500, 502, 'model_upstream_failed'),
    (302, 502, 'model_upstream_failed'),
    ('body_not_json', 502, 'model_response_invalid'),
    ('empty_choices', 502, 'model_response_invalid'),
    ('message_wrong_type', 502, 'model_response_invalid'),
    ('content_wrong_type', 502, 'model_response_invalid'),
    ('content_not_json', 502, 'model_response_invalid'),
    ('invalid_schema', 502, 'model_response_invalid'),
    ('output_rejected', 502, 'model_output_rejected'),
    ('program_bug', 500, 'internal_error'),
])
def test_required_model_errors_are_classified(tmp_path, monkeypatch, caplog, failure, status, code):
    settings = Settings(data_dir=tmp_path, model_base_url='https://test.invalid/v1',
                        model_name='test', model_api_key=SECRET, scheduler_enabled=False)
    original = httpx.Client
    dispatches = []

    def wire(request):
        dispatches.append(request)
        data = json.loads(json.loads(request.content)['messages'][1]['content'])
        if data['task'] == 'safety_classification':
            content = {'findings': [], 'uncertain': False}
        else:
            exceptions = {'connect': httpx.ConnectError, 'read_timeout': httpx.ReadTimeout,
                          'connect_timeout': httpx.ConnectTimeout, 'write_timeout': httpx.WriteTimeout,
                          'pool_timeout': httpx.PoolTimeout, 'program_bug': TypeError}
            if failure in exceptions:
                raise exceptions[failure](SECRET)
            if isinstance(failure, int):
                return httpx.Response(failure, text=SECRET, headers={'Location': 'https://redirect.invalid/'})
            if failure == 'body_not_json':
                return httpx.Response(200, text=SECRET)
            if failure == 'empty_choices':
                return httpx.Response(200, json={'choices': []})
            if failure == 'message_wrong_type':
                return httpx.Response(200, json={'choices': [{'message': SECRET}]})
            if failure == 'content_wrong_type':
                return httpx.Response(200, json={'choices': [{'message': {'content': {'secret': SECRET}}}]})
            if failure == 'content_not_json':
                return httpx.Response(200, json={'choices': [{'message': {'content': SECRET}}]})
            content = {} if failure == 'invalid_schema' else {'actions': [
                {'intent': 'query', 'text': SECRET},
            ]}
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(content)}}]})

    monkeypatch.setattr('app.modeling.parser.httpx.Client', lambda *a, **kw:
                        original(*a, **kw, transport=httpx.MockTransport(wire)))
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        response = client.post('/api/chat', json={'request_id': str(uuid4()), 'text': '今天体重65公斤'})
        assert response.status_code == status
        assert response.json()['code'] == code
        assert response.json()['request_id'] == response.headers['x-request-id']
        assert SECRET not in response.text
        assert SECRET not in caplog.text
        assert SECRET not in json.dumps(client.get('/api/workflows').json())
        assert client.get('/api/events').json() == []
        assert len(dispatches) == 2  # classifier + required plan; no redirect/retry


@pytest.mark.parametrize('kind', [ModelUnavailableError, TypeError])
def test_local_fallbacks_only_catch_known_model_errors(kind):
    def fail(*args, **kwargs):
        raise kind()

    parser = SimpleNamespace(mode='model', boundary=SimpleNamespace(grant=lambda *a: None),
                             phrase_reply=fail, classify_safety=fail)
    plan = make_plan([{'topic': 'salty', 'stance': 'experience'}], 'recorded')
    meal = EventDraft.model_validate({
        'kind': 'meal', 'occurred_at': '2026-10-27T12:00:00+08:00', 'raw_text': '100g米饭',
        'items': [{'name': '米饭', 'quantity': 100, 'unit': 'g'}],
    }).model_dump(mode='json')
    state = {'input_text': '今天体重65公斤', 'safety_context': {'sources': []},
             'safety_fingerprint': 'test', 'process_trace': []}
    operations = [lambda: express_plan(parser, plan),
                  lambda: prepare_nutrition(meal, 'model', fail),
                  lambda: SafetyWorkflowMixin.classify_safety(SimpleNamespace(parser=parser), state)]
    for operation in operations:
        if kind is TypeError:
            with pytest.raises(TypeError):
                operation()
        else:
            result = operation()
            assert result


@pytest.mark.parametrize(('kind', 'status'), [(ModelUnavailableError, 200), (TypeError, 500)])
def test_advice_fallback_does_not_hide_program_errors(tmp_path, monkeypatch, kind, status):
    settings = Settings(data_dir=tmp_path, model_base_url='https://test.invalid/v1',
                        model_name='test', model_api_key='test-only', scheduler_enabled=False)
    original = httpx.Client

    def wire(request):
        data = json.loads(json.loads(request.content)['messages'][1]['content'])
        if data['task'] == 'safety_classification':
            content = {'findings': [], 'uncertain': False}
        else:
            assert data['task'] == 'processing_plan'
            content = {'actions': [{'intent': 'advice', 'text': data['text']}]}
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(content)}}]})

    monkeypatch.setattr('app.modeling.parser.httpx.Client', lambda *a, **kw:
                        original(*a, **kw, transport=httpx.MockTransport(wire)))
    with TestClient(create_app(settings)) as client:
        def fail(*args, **kwargs):
            raise kind()

        monkeypatch.setattr(client.app.state.engine.parser, 'answer_advice', fail)
        response = client.post('/api/chat', json={'request_id': str(uuid4()), 'text': '今天吃饭注意什么'})
        assert response.status_code == status
        if status == 500:
            assert response.json()['code'] == 'internal_error'
        assert client.get('/api/events').json() == []
