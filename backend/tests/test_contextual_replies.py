import json
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.conversation import reconcile_feedback
from app.main import create_app
from app.replies import check_phrasing, make_plan


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, model_base_url='https://test.invalid/v1',
                    model_name='qwen3.8-max', model_api_key='test-only-key', scheduler_enabled=False)


def meal(items=None):
    return {'kind': 'meal', 'occurred_at': '2026-10-08T12:00:00+08:00',
            'raw_text': '昨天吃得有点咸', 'items': items or []}


def install_model(monkeypatch, parse=None, phrase=None):
    calls = {'parse': [], 'patch': [], 'nutrition': [], 'phrase': []}
    original = httpx.Client

    def handler(request):
        body = json.loads(request.content)
        assert body['enable_thinking'] is False
        data = json.loads(body['messages'][1]['content'])
        task = data.get('task')
        if task == 'contextual_reply':
            calls['phrase'].append(data)
            content = phrase(data) if phrase else {'segments': [
                {'point_id': p['point_id'], 'text': p['text']} for p in data['plan']['points']]}
        elif task == 'estimate_nutrition':
            calls['nutrition'].append(data)
            content = {'items': [{'index': item['index'], 'kcal': 280, 'protein_g': 20,
                                 'carbs_g': 10, 'fat_g': 18, 'uncertainty': '常见做法粗估'}
                                for item in data['items']]}
        elif 'requested_fields' in data:
            calls['patch'].append(data)
            content = {'items': [{'name': '炸鸡'}]} if data['requested_fields'] == ['items'] else {
                'quantity': 100, 'unit': 'g'}
        else:
            calls['parse'].append(data)
            content = parse(data) if parse else {'intent': 'record', 'events': [meal()]}
        if isinstance(content, httpx.Response):
            return content
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(content, ensure_ascii=False)}}]})

    monkeypatch.setattr('app.parser.httpx.Client',
                        lambda *a, **kw: original(*a, **kw, transport=httpx.MockTransport(handler)))
    return calls


def chat(client, text, request_id=None):
    payload = {'request_id': request_id or str(uuid4()), 'text': text}
    response = client.post('/api/chat', json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def resume(client, pending, text, request_id=None):
    response = client.post(f'/api/workflows/{pending["thread_id"]}/resume', json={
        'request_id': request_id or str(uuid4()), 'interrupt_id': pending['pending_input']['interrupt_id'],
        'text': text,
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_salty_feedback_is_optional_and_survives_restart_until_recorded(settings, monkeypatch):
    calls = install_model(monkeypatch)
    with TestClient(create_app(settings)) as client:
        request_id = str(uuid4())
        pending = chat(client, '昨天吃得有点咸', request_id)
        assert pending['status'] == 'waiting_input'
        assert pending['pending_input']['optional'] is True
        assert '盐和酱料' in pending['reply']
        assert '不能据此认定' in pending['reply']
        assert '不想记录也可以' in pending['reply']
        assert client.get('/api/events').json() == []
        assert chat(client, '昨天吃得有点咸', request_id) == pending
        assert len(calls['phrase']) == 1
        assert any(t['title'] == '针对性回应' and t['source'] == 'model' for t in pending['process_trace'])
    with TestClient(create_app(settings)) as client:
        pending = resume(client, pending, '炸鸡')
        assert pending['status'] == 'waiting_input'
        assert '炸鸡大约吃了多少' in pending['reply']
        assert '下一餐' not in pending['reply']  # Do not repeat the guidance each turn.
        assert len(calls['phrase']) == 2  # Interrupt replay did not repeat phrasing.
        request_id = str(uuid4())
        done = resume(client, pending, '吃了100g', request_id)
        assert done['status'] == 'completed'
        assert '280 kcal' in done['reply']
        assert '偏咸' in done['reply']
        assert '蛋白质食物' not in done['reply']
        assert '参考你' not in done['reply']
        assert '尚未确认热量目标' not in done['reply']
        assert done['cards'][0]['totals']['kcal'] == 280
        record = client.get('/api/events').json()[0]
        assert record['day'] == '2026-10-08'
        assert record['data']['feedback'] == [{
            'topic': 'salty', 'quote': '昨天吃得有点咸', 'day': '2026-10-08',
            'stance': 'experience', 'source': 'user_report'}]
        assert client.app.state.engine.repo.facts() == []
        assert calls['phrase'][-1]['plan']['context']['foods'] == ['炸鸡']
        assert resume(client, pending, '吃了100g', request_id) == done
        assert len(calls['phrase']) == 3
        assert len(calls['nutrition']) == 1


@pytest.mark.parametrize('answer', ['不知道', '不记了', '先不记，只想知道怎么办'])
def test_optional_feedback_followup_can_end_without_forcing_food(settings, monkeypatch, answer):
    install_model(monkeypatch)
    with TestClient(create_app(settings)) as client:
        pending = chat(client, '昨天吃得有点咸')
        done = resume(client, pending, answer)
        assert done['status'] == 'completed'
        assert '没有保存' in done['reply']
        assert '偏咸' in done['reply']
        assert client.get('/api/events').json() == []


def test_direct_advice_is_not_a_logging_form(settings, monkeypatch):
    calls = install_model(monkeypatch)
    with TestClient(create_app(settings)) as client:
        # Even a model that returns an incomplete meal cannot force a record here.
        result = chat(client, '昨天吃得有点咸，今天应该怎么调整？')
        assert result['status'] == 'completed'
        assert '盐和酱料' in result['reply']
        assert '热量目标' not in result['reply']
        assert '主要吃了什么' not in result['reply']
        assert client.get('/api/events').json() == []
        assert len(calls['phrase']) == 1


def test_explicit_record_request_keeps_required_completeness(settings, monkeypatch):
    install_model(monkeypatch)
    with TestClient(create_app(settings)) as client:
        pending = chat(client, '帮我记录昨天吃得有点咸')
        assert pending['pending_input']['optional'] is False
        assert pending['pending_input']['requested_fields'] == ['items']
        done = resume(client, pending, '不知道')
        assert '没有保存' in done['reply']
        assert client.get('/api/events').json() == []


@pytest.mark.parametrize('bad_reply', [
    '你肯定钠摄入超标了。', '你觉得这餐偏咸，热量为999kcal。',
    '你觉得偏咸，可以多喝水排盐。', '你觉得偏咸，下一餐不要吃主食。',
    '今天的口味偏咸已经记录了。', '你觉得偏咸，可能导致血压升高。',
    '你觉得偏咸，补充低钠盐就可以。',
])
def test_invalid_phrasing_falls_back_without_affecting_saved_values(settings, monkeypatch, bad_reply):
    def phrase(data):
        return {'segments': [{'point_id': p['point_id'],
                              'text': bad_reply if p['role'] == 'ack' else p['text']}
                             for p in data['plan']['points']]}

    install_model(monkeypatch, parse=lambda data: {'intent': 'record', 'events': [meal([
        {'name': '炸鸡', 'quantity': 100, 'unit': 'g'}])]}, phrase=phrase)
    with TestClient(create_app(settings)) as client:
        result = chat(client, '昨天吃了100g炸鸡，有点咸')
        assert result['status'] == 'completed'
        assert bad_reply not in result['reply']
        assert '280 kcal' in result['reply']
        assert client.get('/api/events').json()[0]['data']['items'][0]['kcal'] == 280
        assert any(t['title'] == '针对性回应' and t['source'] == 'local'
                   and '未通过校验' in t['detail'] for t in result['process_trace'])


def test_response_model_failure_does_not_fail_recording(settings, monkeypatch):
    install_model(monkeypatch, parse=lambda data: {'intent': 'record', 'events': [meal([
        {'name': '炸鸡', 'quantity': 100, 'unit': 'g'}])]},
        phrase=lambda data: httpx.Response(504, json={'error': 'upstream-secret-error'}))
    with TestClient(create_app(settings)) as client:
        result = chat(client, '昨天吃了100g炸鸡，有点咸')
        assert result['status'] == 'completed'
        assert '偏咸' in result['reply']
        assert 'upstream-secret-error' not in json.dumps(result)
        assert len(client.get('/api/events').json()) == 1


def test_plain_logging_does_not_call_rephraser_or_quote_unrelated_history(settings, monkeypatch):
    calls = install_model(monkeypatch, parse=lambda data: {'intent': 'record', 'events': [meal([
        {'name': '炸鸡', 'quantity': 100, 'unit': 'g'}])]})
    with TestClient(create_app(settings)) as client:
        result = chat(client, '昨天吃了100g炸鸡')
        assert '280 kcal' in result['reply']
        assert '下一餐' not in result['reply']
        assert '参考你' not in result['reply']
        assert calls['phrase'] == []
        assert client.get('/api/events').json()[0]['data']['feedback'] == []


def test_new_workflow_does_not_inherit_a_previous_meal_concern(settings, monkeypatch):
    calls = install_model(monkeypatch, parse=lambda data: {'intent': 'record', 'events': [meal([
        {'name': '炸鸡', 'quantity': 100, 'unit': 'g'}])]})
    with TestClient(create_app(settings)) as client:
        chat(client, '昨天吃了100g炸鸡，有点咸')
        result = chat(client, '今天吃了100g炸鸡')
        assert '偏咸' not in result['reply']
        assert len(calls['phrase']) == 1


def test_hypothetical_concern_is_not_saved_as_an_experience(settings, monkeypatch):
    install_model(monkeypatch, parse=lambda data: {'intent': 'advice'})
    with TestClient(create_app(settings)) as client:
        result = chat(client, '如果吃得很咸怎么办？')
        assert result['status'] == 'completed'
        assert '你在关注' in result['reply']
        assert '你提到这次' not in result['reply']
        assert client.get('/api/events').json() == []
        assert client.app.state.engine.repo.facts() == []


def test_medical_context_does_not_receive_generic_diet_coaching(settings, monkeypatch):
    install_model(monkeypatch, parse=lambda data: {'intent': 'advice'})
    with TestClient(create_app(settings)) as client:
        result = chat(client, '吃得太咸，肾病需要用什么药？')
        assert '请咨询医生' in result['reply']
        assert '盐和酱料' not in result['reply']
        assert '下一餐' not in result['reply']


@pytest.mark.parametrize('text,topic', [('昨天吃得很咸', 'salty'), ('昨天有点油腻', 'oily'),
                                      ('昨天吃撑了', 'overeating'), ('昨天吃完还是饿', 'hungry')])
def test_feedback_semantics_are_distinct(text, topic):
    signals = reconcile_feedback([], [], text, '2026-10-08')
    assert signals[0]['topic'] == topic
    assert signals[0]['source'] == 'user_report'
    assert signals[0]['day'] == '2026-10-08'
    assert 'sodium' not in signals[0]


def test_negation_retracts_feedback_and_history_cannot_introduce_it():
    previous = reconcile_feedback([], [], '昨天吃得有点咸', '2026-10-08')
    assert reconcile_feedback(previous, [], '其实不咸', '2026-10-08') == []
    invented = {'topic': 'salty', 'quote': '昨天吃得有点咸', 'day': '2026-10-08'}
    assert reconcile_feedback([], [invented], '吃了100g炸鸡', '2026-10-08') == []
    assert reconcile_feedback([], [], '朋友昨天觉得太咸', '2026-10-08') == []


def test_new_topic_has_its_own_guidance_and_previous_guidance_is_not_repeated():
    signals = reconcile_feedback([], [], '昨天有点咸，也有点油腻', '2026-10-08')
    plan = make_plan(signals, 'clarify', guided_topics=['salty'])
    assert [p['point_id'] for p in plan['points']] == ['ack_salty', 'ack_oily', 'guide_oily']
    assert plan['boundary'] == ''


def test_unapproved_or_missing_response_points_are_rejected():
    signals = reconcile_feedback([], [], '昨天有点咸', '2026-10-08')
    plan = make_plan(signals, 'advice')
    with pytest.raises(ValueError):
        check_phrasing({'segments': [{'point_id': 'ack_salty', 'text': '你觉得偏咸。'}]}, plan)
    with pytest.raises(ValueError):
        check_phrasing({'segments': [{'point_id': 'unknown', 'text': '你觉得偏咸。'}]}, plan)


def test_valid_contextual_phrasing_is_adopted_but_receipt_remains_local(settings, monkeypatch):
    def phrase(data):
        return {'segments': [{'point_id': 'ack_salty', 'text': '你说这餐炸鸡的口味有些偏咸。'},
                             {'point_id': 'guide_salty', 'text': '下一餐可以减少盐和酱料，选少盐的做法。'}]}
    install_model(monkeypatch, parse=lambda data: {'intent': 'record', 'events': [meal([
        {'name': '炸鸡', 'quantity': 100, 'unit': 'g'}])]}, phrase=phrase)
    with TestClient(create_app(settings)) as client:
        result = chat(client, '昨天吃了100g炸鸡，有点咸')
        assert '已记下昨天的100g炸鸡。' in result['reply']
        assert '你说这餐炸鸡的口味有些偏咸。' in result['reply']
        assert '280 kcal' in result['reply']
        assert any(t['title'] == '针对性回应' and t['source'] == 'model' for t in result['process_trace'])
