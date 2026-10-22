import json
from datetime import datetime
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.common.config import Settings
from app.common.clock import SHANGHAI
from app.main import create_app
from model_helpers import planned_content


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    fixed = lambda: datetime(2026, 10, 9, 12, tzinfo=SHANGHAI)
    for module in ('app.common.clock', 'app.modeling.parser'):
        monkeypatch.setattr(module + '.now', fixed)


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, model_base_url='https://test.invalid/v1',
                    model_name='qwen3.8-max', model_api_key='test-only-key', scheduler_enabled=False)


def mock_model(monkeypatch, handler):
    original = httpx.Client
    def isolated_handler(request):
        data = json.loads(json.loads(request.content)['messages'][1]['content'])
        if data.get('task') == 'contextual_reply':
            return model_response({'segments': [{'point_id': p['point_id'], 'text': p['text']}
                                               for p in data['plan']['points']]})
        if data.get('task') == 'estimate_nutrition':
            # These tests isolate extraction/patch behavior; nutrition has its own integration tests.
            return model_response({'items': [{'index': item['index'], 'uncertainty': '测试中不估算营养'}
                                             for item in data['items']]})
        response = handler(request)
        content = json.loads(response.json()['choices'][0]['message']['content'])
        return model_response(planned_content(data, content))
    monkeypatch.setattr('app.modeling.parser.httpx.Client',
                        lambda *a, **kw: original(*a, **kw, transport=httpx.MockTransport(isolated_handler)))


def model_response(content):
    return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(content, ensure_ascii=False)}}]})


def chat(client, text):
    response = client.post('/api/chat', json={'request_id': str(uuid4()), 'text': text})
    assert response.status_code == 200, response.text
    return response.json()


def resume(client, pending, text, request_id=None):
    return client.post(f'/api/workflows/{pending["thread_id"]}/resume', json={
        'request_id': request_id or str(uuid4()),
        'interrupt_id': pending['pending_input']['interrupt_id'], 'text': text,
    })


def meal(text='我昨天吃的有点咸', **fields):
    return {'kind': 'meal', 'occurred_at': '2026-10-08T12:00:00+08:00',
            'raw_text': text, 'items': [], **fields}


def test_missing_food_survives_restart_and_preserves_date_and_original_text(settings, monkeypatch):
    calls = []

    def handler(request):
        body = json.loads(request.content)
        assert body['enable_thinking'] is False
        data = json.loads(body['messages'][1]['content'])
        calls.append(data)
        if len(calls) == 1:
            # Even without a model-generated question, local completeness checks must ask.
            return model_response({'intent': 'record', 'events': [meal()]})
        assert data['requested_fields'] == ['items']
        assert data['draft']['occurred_at'] == '2026-10-08T12:00:00+08:00'
        assert data['draft']['raw_text'] == '我昨天吃的有点咸'
        assert 'recent_records' not in data
        return model_response({'items': [{'name': '牛肉面', 'quantity': 1, 'unit': '碗'}],
                               'occurred_at': '2026-10-10T12:00:00+08:00'})

    mock_model(monkeypatch, handler)
    with TestClient(create_app(settings)) as client:
        pending = chat(client, '我昨天吃的有点咸')
        assert pending['status'] == 'waiting_input'
        assert pending['pending_input']['requested_fields'] == ['items']
        assert client.get('/api/events').json() == []
    with TestClient(create_app(settings)) as client:
        request_id = str(uuid4())
        response = resume(client, pending, '一碗牛肉面', request_id)
        assert response.status_code == 200, response.text
        assert response.json()['status'] == 'completed'
        # A repeated resume must neither invoke the model again nor duplicate the event.
        assert resume(client, pending, '一碗牛肉面', request_id).json() == response.json()
        records = client.get('/api/events').json()
        assert len(records) == 1
        assert records[0]['day'] == '2026-10-08'
        assert '我昨天吃的有点咸' in records[0]['raw_text']
        assert '一碗牛肉面' in records[0]['raw_text']
    assert len(calls) == 2


def test_two_unhelpful_answers_cannot_bypass_required_fields(settings, monkeypatch):
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        if count == 1:
            return model_response({'intent': 'record', 'events': [meal()]})
        if count < 4:
            return model_response({})
        data = json.loads(json.loads(request.content)['messages'][1]['content'])
        assert '再想想' in data['draft']['raw_text']
        return model_response({'items': [{'name': '牛肉面', 'quantity': 1, 'unit': '碗'}]})

    mock_model(monkeypatch, handler)
    with TestClient(create_app(settings)) as client:
        pending = chat(client, '我昨天吃的有点咸')
        for answer in ['稍等', '我再想想']:
            response = resume(client, pending, answer)
            assert response.status_code == 200, response.text
            pending = response.json()
            assert pending['status'] == 'waiting_input'
            assert client.get('/api/events').json() == []
        done = resume(client, pending, '一碗牛肉面')
        assert done.status_code == 200, done.text
        assert done.json()['status'] == 'completed'
        assert len(client.get('/api/events').json()) == 1


@pytest.mark.parametrize('kind,fields,requested,answer,patch', [
    ('weight', {}, 'value', '65公斤', {'value': 65}),
    ('sleep', {}, 'value', '6小时', {'value': 360}),
    ('exercise', {'intensity': 'low'}, 'value', '30分钟', {'value': 30}),
    ('cycle', {}, 'cycle_action', '开始了', {'cycle_action': 'start'}),
    ('meal', {'occurred_at': None, 'items': [{'name': '苹果'}]}, 'occurred_at',
     '2026-10-08', {'occurred_at': '2026-10-08T12:00:00+08:00'}),
])
def test_other_required_fields_are_collected_before_saving(settings, monkeypatch, kind, fields, requested, answer, patch):
    def handler(request):
        data = json.loads(json.loads(request.content)['messages'][1]['content'])
        if 'requested_fields' in data:
            assert data['requested_fields'] == [requested]
            return model_response(patch)
        event = {'kind': kind, 'occurred_at': '2026-10-08T12:00:00+08:00',
                 'raw_text': data['text'], **fields}
        return model_response({'intent': 'record', 'events': [event]})

    mock_model(monkeypatch, handler)
    with TestClient(create_app(settings)) as client:
        prompt = ('苹果待补记录' if kind == 'meal' else
                  '轻松待补记录' if kind == 'exercise' else '待补记录')
        pending = chat(client, prompt)
        assert pending['status'] == 'waiting_input'
        assert client.get('/api/events').json() == []
        done = resume(client, pending, answer)
        assert done.status_code == 200, done.text
        assert done.json()['status'] == 'completed'
        assert len(client.get('/api/events').json()) == 1


def test_mixed_records_keep_stable_indexes_and_do_not_reextract_saved_records(settings, monkeypatch):
    def handler(request):
        data = json.loads(json.loads(request.content)['messages'][1]['content'])
        if 'requested_fields' in data:
            assert data['draft']['kind'] == 'meal'
            assert data['requested_fields'] == ['items']
            return model_response({'items': [{'name': '苹果'}]})
        return model_response({'intent': 'record', 'events': [
            meal(), {'kind': 'weight', 'occurred_at': '2026-10-09T07:00:00+08:00',
                     'raw_text': '今天体重65公斤', 'value': 65},
        ]})

    mock_model(monkeypatch, handler)
    with TestClient(create_app(settings)) as client:
        pending = chat(client, '昨天吃得咸，今天体重65公斤')
        records = client.get('/api/events').json()
        assert [r['kind'] for r in records] == ['weight']
        weight_id = records[0]['id']
        assert pending['pending_input']['saved_records'] == 1
        done = resume(client, pending, '苹果')
        assert done.status_code == 200, done.text
        records = client.get('/api/events').json()
        assert len(records) == 2
        assert next(r['id'] for r in records if r['kind'] == 'weight') == weight_id
        assert next(r['day'] for r in records if r['kind'] == 'meal') == '2026-10-08'


def test_multiple_optional_meals_are_not_saved_until_their_own_answers(settings, monkeypatch):
    def handler(request):
        data = json.loads(json.loads(request.content)['messages'][1]['content'])
        if 'requested_fields' in data:
            quantity = 1 if data['draft']['raw_text'] == '昨天午餐牛肉面' else 2
            return model_response({'quantity': quantity, 'unit': '碗'})
        return model_response({'intent': 'record', 'events': [
            meal('昨天午餐牛肉面', items=[{'name': '牛肉面'}]),
            meal('昨天晚餐牛肉面', occurred_at='2026-10-08T18:00:00+08:00', items=[{'name': '牛肉面'}]),
        ]})

    mock_model(monkeypatch, handler)
    with TestClient(create_app(settings)) as client:
        pending = chat(client, '昨天午餐和晚餐都吃了牛肉面')
        assert client.get('/api/events').json() == []
        response = resume(client, pending, '午餐一碗')
        assert response.status_code == 200, response.text
        pending = response.json()
        assert pending['status'] == 'waiting_input'
        first = client.get('/api/events').json()
        assert len(first) == 1
        assert first[0]['data']['items'][0]['quantity'] == 1
        done = resume(client, pending, '晚餐两碗')
        assert done.status_code == 200, done.text
        records = client.get('/api/events').json()
        assert len(records) == 2
        assert sorted(r['data']['items'][0]['quantity'] for r in records) == [1, 2]


def test_portion_patch_does_not_rewrite_other_foods_or_date(settings, monkeypatch):
    def handler(request):
        data = json.loads(json.loads(request.content)['messages'][1]['content'])
        if 'requested_fields' in data:
            assert data['item_index'] == 0
            return model_response({'quantity': 1, 'unit': '碗',
                                   'items': [{'name': '炸鸡', 'quantity': 3}],
                                   'occurred_at': '2026-10-09T12:00:00+08:00'})
        return model_response({'intent': 'record', 'events': [meal(items=[
            {'name': '牛肉面'}, {'name': '卤蛋', 'unit': '个'},
        ])]})

    mock_model(monkeypatch, handler)
    with TestClient(create_app(settings)) as client:
        pending = chat(client, '昨天吃了牛肉面和卤蛋')
        done = resume(client, pending, '牛肉面一碗')
        assert done.status_code == 200, done.text
        record = client.get('/api/events').json()[0]
        assert record['day'] == '2026-10-08'
        assert [i['name'] for i in record['data']['items']] == ['牛肉面', '卤蛋']
        assert record['data']['items'][0]['quantity'] == 1
        assert record['data']['items'][1]['quantity'] is None


@pytest.mark.parametrize('answer', ['不知道', '不记了'])
def test_abandoned_required_draft_never_creates_an_empty_meal(settings, monkeypatch, answer):
    mock_model(monkeypatch, lambda request: model_response({'intent': 'record', 'events': [meal()]}))
    with TestClient(create_app(settings)) as client:
        pending = chat(client, '我昨天吃的有点咸')
        done = resume(client, pending, answer)
        assert done.status_code == 200, done.text
        assert done.json()['status'] == 'completed'
        assert '没有保存' in done.json()['reply']
        assert client.get('/api/events').json() == []


def test_unknown_optional_portion_is_saved_as_unknown(settings, monkeypatch):
    mock_model(monkeypatch, lambda request: model_response({
        'intent': 'record', 'events': [meal(items=[{'name': '牛肉面'}])],
    }))
    with TestClient(create_app(settings)) as client:
        pending = chat(client, '昨天吃了牛肉面')
        done = resume(client, pending, '不知道')
        assert done.status_code == 200, done.text
        assert done.json()['status'] == 'completed'
        record = client.get('/api/events').json()[0]
        assert record['data']['items'][0]['quantity'] is None


def test_nested_portion_patch_need_not_repeat_known_food_name(settings, monkeypatch):
    def handler(request):
        data = json.loads(json.loads(request.content)['messages'][1]['content'])
        if 'requested_fields' in data:
            return model_response({'items': [{'quantity': 1, 'unit': '碗'}]})
        return model_response({'intent': 'record', 'events': [meal(items=[{'name': '牛肉面'}])]})

    mock_model(monkeypatch, handler)
    with TestClient(create_app(settings)) as client:
        pending = chat(client, '昨天吃了牛肉面')
        done = resume(client, pending, '一碗')
        assert done.status_code == 200, done.text
        assert done.json()['status'] == 'completed'
        item = client.get('/api/events').json()[0]['data']['items'][0]
        assert item['name'] == '牛肉面'
        assert item['quantity'] == 1
        assert item['unit'] == '碗'


def test_unnamed_food_draft_retains_known_quantity_when_name_is_added(settings, monkeypatch):
    def handler(request):
        data = json.loads(json.loads(request.content)['messages'][1]['content'])
        if 'requested_fields' in data:
            assert data['item_index'] == 1
            if data['requested_fields'] == ['items']:
                return model_response({'items': [{'name': '牛肉面'}]})
            assert data['requested_fields'] == ['quantity']
            return model_response({'quantity': 2, 'unit': '碗'})
        return model_response({'intent': 'record', 'events': [meal(items=[
            {'name': '卤蛋', 'quantity': 1, 'unit': '个'}, {'quantity': 2, 'unit': '碗'},
        ])]})

    mock_model(monkeypatch, handler)
    with TestClient(create_app(settings)) as client:
        pending = chat(client, '昨天吃了一个卤蛋和两碗别的东西')
        assert client.get('/api/events').json() == []
        named = resume(client, pending, '牛肉面')
        assert named.json()['status'] == 'waiting_input'
        done = resume(client, named.json(), '两碗')
        assert done.status_code == 200, done.text
        items = client.get('/api/events').json()[0]['data']['items']
        assert [i['name'] for i in items] == ['卤蛋', '牛肉面']
        assert [i['quantity'] for i in items] == [1, 2]
        assert items[1]['unit'] == '碗'


def test_invalid_supplied_value_is_rejected_even_during_clarification(settings, monkeypatch):
    def handler(request):
        data = json.loads(json.loads(request.content)['messages'][1]['content'])
        if 'requested_fields' in data:
            return model_response({'value': 9999})
        return model_response({'intent': 'record', 'events': [{
            'kind': 'weight', 'occurred_at': '2026-10-08T07:00:00+08:00', 'raw_text': '昨天称过体重',
        }]})

    mock_model(monkeypatch, handler)
    with TestClient(create_app(settings)) as client:
        pending = chat(client, '昨天称过体重')
        response = resume(client, pending, '9999公斤')
        assert response.status_code == 409
        assert client.get('/api/events').json() == []
