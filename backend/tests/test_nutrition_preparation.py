import json
from datetime import datetime
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.common.config import Settings
from app.common.clock import SHANGHAI
from app.main import create_app
from model_helpers import planned_content, safety_transport


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    fixed = lambda: datetime(2026, 10, 9, 12, tzinfo=SHANGHAI)
    for module in ('app.common.clock', 'app.modeling.parser'):
        monkeypatch.setattr(module + '.now', fixed)


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, model_base_url='https://test.invalid/v1',
                    model_name='qwen3.8-max', model_api_key='test-only-key', scheduler_enabled=False)


def meal(quantity=100, **extra):
    return {'kind': 'meal', 'occurred_at': '2026-10-08T12:00:00+08:00', 'raw_text': '昨天吃了炸鸡',
            'items': [{'name': '炸鸡', 'quantity': quantity, 'unit': 'g', **extra}]}


def rough(index, kcal=320):
    factor = kcal / 320
    return {'index': index, 'kcal': kcal, 'protein_g': 20 * factor,
            'carbs_g': 15 * factor, 'fat_g': 20 * factor, 'uncertainty': '常见做法粗估，裹粉与含油量会影响结果'}


def mock_model(monkeypatch, extract, estimate):
    calls = {'parse': 0, 'nutrition': 0}
    original = httpx.Client

    def handler(request):
        body = json.loads(request.content)
        assert body['enable_thinking'] is False
        data = json.loads(body['messages'][1]['content'])
        if data.get('task') == 'contextual_reply':
            # Nutrition tests do not count or exercise the independent phrasing layer.
            content = {'segments': [{'point_id': p['point_id'], 'variant_id': 'default'}
                                    for p in data['plan']['points']]}
        elif data.get('task') == 'estimate_nutrition':
            calls['nutrition'] += 1
            content = estimate(data['items'])
        else:
            calls['parse'] += 1
            content = extract(data)
        if isinstance(content, httpx.Response):
            return content
        content = planned_content(data, content)
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(content)}}]})

    monkeypatch.setattr('app.modeling.parser.httpx.Client',
                        lambda *a, **kw: original(*a, **kw, transport=httpx.MockTransport(safety_transport(handler))))
    return calls


def chat(client, text, request_id=None, event_id=None):
    response = client.post('/api/chat', json={'request_id': request_id or str(uuid4()), 'text': text, 'event_id': event_id})
    assert response.status_code == 200, response.text
    return response.json()


def resume(client, result, text):
    response = client.post(f'/api/workflows/{result["thread_id"]}/resume', json={
        'request_id': str(uuid4()), 'interrupt_id': result['pending_input']['interrupt_id'], 'text': text,
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_complete_meal_is_estimated_before_write_and_reply_matches_saved_values(settings, monkeypatch):
    client = None

    def estimate(items):
        assert client.get('/api/events').json() == []
        assert items[0]['quantity'] == 100
        assert items[0]['unit'] == 'g'
        return {'items': [rough(0)]}

    calls = mock_model(monkeypatch, lambda data: {'intent': 'record', 'events': [meal()]}, estimate)
    with TestClient(create_app(settings)) as client:
        request_id = str(uuid4())
        result = chat(client, '昨天吃了100g炸鸡', request_id)
        assert result['status'] == 'completed'
        assert result['cards'][0]['totals']['kcal'] == 320
        assert '320 kcal' in result['reply']
        assert '粗估' in result['reply']
        record = client.get('/api/events').json()[0]
        item = record['data']['items'][0]
        assert item['nutrition_status'] == 'estimated'
        assert item['nutrition_sources']['kcal'] == 'model_estimate'
        assert item['kcal_range'] == [240, 400]
        assert result['cards'][0]['items'] == record['data']['items']
        assert chat(client, '昨天吃了100g炸鸡', request_id) == result
        for endpoint in ['/api/events', '/api/messages', '/api/dashboard', '/api/trends']:
            assert client.get(endpoint).status_code == 200
    assert calls == {'parse': 1, 'nutrition': 1}


def test_nutrition_waits_for_food_and_portion_then_uses_complete_draft(settings, monkeypatch):
    def extract(data):
        if 'requested_fields' not in data:
            event = meal()
            event['items'] = []
            return {'intent': 'record', 'events': [event]}
        if data['requested_fields'] == ['items']:
            return {'items': [{'name': '炸鸡'}]}
        return {'items': [{'quantity': 100, 'unit': 'g'}]}

    def estimate(items):
        assert items[0]['name'] == '炸鸡'
        assert items[0]['quantity'] == 100
        return {'items': [rough(0)]}

    calls = mock_model(monkeypatch, extract, estimate)
    with TestClient(create_app(settings)) as client:
        pending = chat(client, '昨天吃得咸')
        assert calls['nutrition'] == 0
        pending = resume(client, pending, '炸鸡')
        assert pending['status'] == 'waiting_input'
        assert calls['nutrition'] == 0
        done = resume(client, pending, '吃了100g')
        assert done['cards'][0]['totals']['kcal'] == 320
        record = client.get('/api/events').json()[0]
        assert record['day'] == '2026-10-08'
        assert record['data']['items'][0]['kcal'] == 320
    assert calls['nutrition'] == 1


def test_save_ready_uses_nutrition_preparation_and_does_not_repeat_on_resume(settings, monkeypatch):
    def extract(data):
        if 'requested_fields' in data:
            return {'intensity': 'low'}
        return {'intent': 'record', 'events': [meal(), {
            'kind': 'exercise', 'occurred_at': '2026-10-09T19:00:00+08:00',
            'raw_text': '跑步30分钟', 'value': 30,
        }]}

    calls = mock_model(monkeypatch, extract, lambda items: {'items': [rough(0)]})
    with TestClient(create_app(settings)) as client:
        pending = chat(client, '昨天吃了100g炸鸡，今天跑步30分钟')
        assert pending['status'] == 'waiting_input'
        record = client.get('/api/events').json()[0]
        assert record['data']['items'][0]['kcal'] == 320
        assert calls['nutrition'] == 1
        done = resume(client, pending, '轻松')
        assert done['status'] == 'completed'
        assert len(client.get('/api/events').json()) == 2
        assert done['cards'][0]['totals']['kcal'] == 320
    assert calls['nutrition'] == 1


def test_portion_correction_invalidates_old_estimate_and_preserves_record_id(settings, monkeypatch):
    record_id = None

    def extract(data):
        if record_id:
            # Even if a parser copies stale nutrients, preparation must not adopt them.
            return {'intent': 'correct', 'correction_id': record_id,
                    'events': [meal(200, kcal=320, source='model_estimate')]}
        return {'intent': 'record', 'events': [meal()]}

    calls = mock_model(monkeypatch, extract,
                       lambda items: {'items': [rough(0, 320 * items[0]['quantity'] / 100)]})
    with TestClient(create_app(settings)) as client:
        chat(client, '昨天吃了100g炸鸡')
        record_id = client.get('/api/events').json()[0]['id']
        done = chat(client, '不是100g，是200g')
        record = client.get('/api/events').json()[0]
        assert record['id'] == record_id
        assert record['version'] == 2
        assert record['data']['items'][0]['kcal'] == 640
        assert done['cards'][0]['totals']['kcal'] == 640
        assert '640 kcal' in done['reply']
    assert calls['nutrition'] == 2


@pytest.mark.parametrize('change', [{'name': '豆腐'}, {'cooking': '不同做法'}])
def test_food_or_cooking_change_recomputes_estimate(settings, monkeypatch, change):
    record_id = None

    def extract(data):
        return {'intent': 'correct' if record_id else 'record',
                'correction_id': record_id, 'events': [meal(**change) if record_id else meal()]}

    calls = mock_model(monkeypatch, extract, lambda items: {'items': [rough(0)]})
    with TestClient(create_app(settings)) as client:
        chat(client, '昨天吃了100g炸鸡')
        record_id = client.get('/api/events').json()[0]['id']
        chat(client, '食物改成豆腐' if 'name' in change else '做法改成不同做法', event_id=record_id)
        assert client.get('/api/events').json()[0]['version'] == 2
    assert calls['nutrition'] == 2


def test_unchanged_nutrition_is_reused_during_unrelated_correction(settings, monkeypatch):
    record_id = None

    def extract(data):
        return {'intent': 'correct' if record_id else 'record',
                'correction_id': record_id, 'events': [meal()]}

    calls = mock_model(monkeypatch, extract, lambda items: {'items': [rough(0)]})
    with TestClient(create_app(settings)) as client:
        chat(client, '昨天吃了100g炸鸡')
        record_id = client.get('/api/events').json()[0]['id']
        chat(client, '只补充记录备注，食物分量不变', event_id=record_id)
        assert client.get('/api/events').json()[0]['data']['items'][0]['kcal'] == 320
    assert calls['nutrition'] == 1


def test_user_value_is_not_overwritten_when_other_nutrients_are_estimated(settings, monkeypatch):
    def estimate(items):
        assert items[0]['provided_nutrients'] == {'kcal': 120}
        assert 'kcal' not in items[0]['requested_nutrients']
        return {'items': [{'index': 0, 'kcal': 9999, 'protein_g': 8, 'carbs_g': 12, 'fat_g': 4,
                           'uncertainty': '缺失三大营养素粗估'}]}

    calls = mock_model(monkeypatch, lambda data: {'intent': 'record',
                       'events': [meal(kcal=120, source='user')]}, estimate)
    with TestClient(create_app(settings)) as client:
        done = chat(client, '昨天吃了100g炸鸡，实际热量120kcal')
        item = client.get('/api/events').json()[0]['data']['items'][0]
        assert item['kcal'] == 120
        assert item['nutrition_sources'] == {'kcal': 'user', 'protein_g': 'model_estimate',
                                             'carbs_g': 'model_estimate', 'fat_g': 'model_estimate'}
        assert item['nutrition_reference']['values']['kcal'] == 120
        assert done['cards'][0]['totals']['kcal'] == 120
    assert calls['nutrition'] == 1


def test_per_100g_user_label_is_scaled_without_model_and_survives_portion_change(settings, monkeypatch):
    record_id = None

    def extract(data):
        event = meal(300 if record_id else 200, source='user', nutrition_basis='per_100g',
                     kcal=100, protein_g=10, carbs_g=10, fat_g=2)
        return {'intent': 'correct' if record_id else 'record', 'correction_id': record_id, 'events': [event]}

    calls = mock_model(monkeypatch, extract, lambda items: pytest.fail('完整用户标签不应调用估算模型'))
    with TestClient(create_app(settings)) as client:
        first = chat(client, '昨天吃了200g炸鸡，每100g热量100kcal，蛋白质10g，碳水10g，脂肪2g')
        assert first['cards'][0]['totals']['kcal'] == 200
        record_id = client.get('/api/events').json()[0]['id']
        done = chat(client, '不是200g，是300g')
        item = client.get('/api/events').json()[0]['data']['items'][0]
        assert item['kcal'] == 300
        assert item['nutrition_basis'] == 'consumed'
        assert item['nutrition_reference']['values']['kcal'] == 100
        assert item['nutrition_reference']['basis'] == 'per_100g'
        assert item['nutrition_sources']['kcal'] == 'user'
        assert done['cards'][0]['totals']['kcal'] == 300
    assert calls['nutrition'] == 0


def test_repeating_same_user_value_reuses_completed_mixed_nutrition(settings, monkeypatch):
    record_id = None

    def extract(data):
        return {'intent': 'correct' if record_id else 'record', 'correction_id': record_id,
                'events': [meal(kcal=120, source='user')]}

    calls = mock_model(monkeypatch, extract, lambda items: {'items': [{
        'index': 0, 'protein_g': 8, 'carbs_g': 12, 'fat_g': 4, 'uncertainty': '缺失营养粗估',
    }]})
    with TestClient(create_app(settings)) as client:
        chat(client, '昨天吃了100g炸鸡，实际热量120kcal')
        record_id = client.get('/api/events').json()[0]['id']
        chat(client, '确认还是100g，热量120kcal', event_id=record_id)
        item = client.get('/api/events').json()[0]['data']['items'][0]
        assert item['kcal'] == 120
        assert item['protein_g'] == 8
        assert item['nutrition_sources']['kcal'] == 'user'
    assert calls['nutrition'] == 1


def test_consumed_user_values_are_preserved_but_not_applied_to_new_portion(settings, monkeypatch):
    record_id = None

    def extract(data):
        event = meal(200) if record_id else meal(kcal=320, protein_g=20, carbs_g=15, fat_g=20, source='user')
        return {'intent': 'correct' if record_id else 'record', 'correction_id': record_id, 'events': [event]}

    calls = mock_model(monkeypatch, extract, lambda items: pytest.fail('不要覆盖用户原来的实际总量'))
    with TestClient(create_app(settings)) as client:
        chat(client, '100g炸鸡，实际热量320kcal，蛋白质20g，碳水15g，脂肪20g')
        record_id = client.get('/api/events').json()[0]['id']
        done = chat(client, '分量改成200g', event_id=record_id)
        item = client.get('/api/events').json()[0]['data']['items'][0]
        assert item['kcal'] is None
        assert item['nutrition_reference']['values']['kcal'] == 320
        assert '旧食物或分量' in item['nutrition_reason']
        assert done['cards'][0]['totals']['kcal'] is None
    assert calls['nutrition'] == 0


def test_unknown_portion_never_defaults_to_one_or_zero(settings, monkeypatch):
    calls = mock_model(monkeypatch, lambda data: {'intent': 'record', 'events': [meal(None)]},
                       lambda items: pytest.fail('分量未知不应调用营养估算'))
    with TestClient(create_app(settings)) as client:
        pending = chat(client, '昨天吃了炸鸡')
        done = resume(client, pending, '不知道')
        item = client.get('/api/events').json()[0]['data']['items'][0]
        assert item['quantity'] is None
        assert item['kcal'] is None
        assert item['nutrition_status'] == 'unavailable'
        assert '分量未知' in done['reply']
    assert calls['nutrition'] == 0


@pytest.mark.parametrize('response', [
    httpx.Response(504, json={'error': {'message': 'timeout'}}),
    {'items': [{**rough(0), 'kcal': -5}]},
    {'items': [{**rough(0), 'kcal': float('nan')}]},
    {'items': [{**rough(0), 'kcal': 10}]},
    {'items': [rough(9)]},
])
def test_bad_estimate_keeps_food_record_and_does_not_publish_fake_values(settings, monkeypatch, response):
    mock_model(monkeypatch, lambda data: {'intent': 'record', 'events': [meal()]}, lambda items: response)
    with TestClient(create_app(settings)) as client:
        done = chat(client, '昨天吃了100g炸鸡')
        assert done['status'] == 'completed'
        item = client.get('/api/events').json()[0]['data']['items'][0]
        assert item['name'] == '炸鸡'
        assert item['quantity'] == 100
        assert item['kcal'] is None
        assert item['nutrition_status'] == 'unavailable'
        assert item['nutrition_reason']
        assert done['cards'][0]['totals']['kcal'] is None
        assert '热量未知' in done['reply']


def test_partial_estimate_is_labelled_partial_and_unknown_is_not_zero(settings, monkeypatch):
    mock_model(monkeypatch, lambda data: {'intent': 'record', 'events': [meal()]},
               lambda items: {'items': [{'index': 0, 'kcal': 320, 'uncertainty': '只能粗估热量'}]})
    with TestClient(create_app(settings)) as client:
        done = chat(client, '昨天吃了100g炸鸡')
        item = client.get('/api/events').json()[0]['data']['items'][0]
        assert item['nutrition_status'] == 'partial'
        assert item['protein_g'] is None
        assert done['cards'][0]['totals']['kcal'] == 320
        assert not done['cards'][0]['totals']['complete_nutrition']
        assert '部分营养未知' in done['reply']


def test_checkpoint_replay_after_commit_does_not_repeat_nutrition_call(settings, monkeypatch):
    calls = mock_model(monkeypatch, lambda data: {'intent': 'record', 'events': [meal()]},
                       lambda items: {'items': [rough(0)]})
    payload = {'request_id': str(uuid4()), 'text': '昨天吃了100g炸鸡'}
    with TestClient(create_app(settings)) as client:
        def fail_format(*args):
            raise ValueError('模拟提交后响应失败')
        monkeypatch.setattr(client.app.state.engine, 'format_result', fail_format)
        assert client.post('/api/chat', json=payload).status_code == 409
        assert len(client.get('/api/events').json()) == 1
    with TestClient(create_app(settings)) as client:
        response = client.post('/api/chat', json=payload)
        assert response.status_code == 200, response.text
        assert len(client.get('/api/events').json()) == 1
        assert response.json()['cards'][0]['totals']['kcal'] == 320
    assert calls == {'parse': 1, 'nutrition': 1}


def test_unknown_food_is_not_treated_as_zero_when_computing_remaining_budget(settings, monkeypatch):
    event = meal()
    event['items'].append({'name': '未知配方炖菜', 'quantity': 100, 'unit': 'g'})
    mock_model(monkeypatch, lambda data: {'intent': 'record', 'events': [event]},
               lambda items: {'items': [rough(0), {'index': 1, 'uncertainty': '配方无法判断'}]})
    with TestClient(create_app(settings)) as client:
        proposed = client.post('/api/plans/propose', json={'request_id': str(uuid4()), 'targets': {
            'kcal': 1800, 'protein_g': 100, 'carbs_g': 215, 'fat_g': 60, 'exercise_minutes': 120,
        }}).json()
        assert client.post(f'/api/workflows/{proposed["thread_id"]}/resume', json={
            'request_id': str(uuid4()), 'interrupt_id': proposed['pending_input']['interrupt_id'], 'approved': True,
        }).status_code == 200
        done = chat(client, '昨天吃了100g炸鸡和100g未知配方炖菜')
        daily = client.get('/api/dashboard?date=2026-10-08').json()
        assert daily['totals']['kcal'] == 320
        assert daily['remaining']['kcal'] is None
        assert '已知食物热量合计约320 kcal' in done['reply']
        assert '部分营养未知' in done['reply']
        assert '下一餐可用蛋白质食物' not in done['reply']
