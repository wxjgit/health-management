"""Safety tests exercise the real model boundary; no production safety bypass."""

from app.common.errors import DomainValidationError, ModelOutputError
import json
from uuid import uuid4
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.common.config import Settings
from app.contracts.safety import SafetyClassification
from app.health.safety import REPLIES, merge_classification, text_risk, offline_record
from app.main import create_app
from app.modeling import PolicyError
from app.health.advice import validate_advice


GENERAL = {'findings': [], 'uncertain': False}
TARGETS = {'kcal': 1800, 'protein_g': 100, 'carbs_g': 215, 'fat_g': 60, 'exercise_minutes': 120}
CASES = json.loads((Path(__file__).parent / "fixtures/safety_cases.json").read_text(encoding="utf-8"))


def profile(**values):
    return {'age': 30, 'sex': 'female', 'height_cm': 170, 'weight_kg': 70,
            'goal_weight_kg': 60, 'goal_date': '2027-01-01', 'activity': 'light',
            'nutrition_context': 'general', **values}


def post(client, text, **values):
    return client.post('/api/chat', json={'request_id': str(uuid4()), 'text': text, **values})


def install(monkeypatch, classify=GENERAL, planner=None):
    calls = []
    original = httpx.AsyncClient
    def handler(request):
        data = json.loads(json.loads(request.content)['messages'][1]['content'])
        calls.append(data)
        if data['task'] == 'safety_classification':
            value = classify(data) if callable(classify) else classify
        elif planner:
            value = planner(data)
        else:
            pytest.fail('Risk branch reached ordinary model task: ' + data['task'])
        if isinstance(value, httpx.Response):
            return value
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(value)}}]})
    monkeypatch.setattr('app.modeling.transport.httpx.AsyncClient',
                        lambda *a, **kw: original(*a, **kw, transport=httpx.MockTransport(handler)))
    return calls


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, scheduler_enabled=False, model_base_url='https://model.test.invalid/v1',
                    model_name='safety-test', model_api_key='test-only')


@pytest.mark.parametrize('text,category', CASES)
def test_independent_local_corpus(text, category):
    assert text_risk(text) == category


def test_explicit_risks_do_not_call_any_model_even_if_it_would_misclassify(settings, monkeypatch):
    calls = install(monkeypatch)
    with TestClient(create_app(settings)) as client:
        for text, category in CASES:
            if category == 'general':
                continue
            result = post(client, text).json()
            assert result['status'] == 'completed'
            assert result['safety']['category'] == category
            assert result['reply'] == REPLIES[category]
            assert result['cards'] == result['evidence_refs'] == result['model_disclosures'] == []
        assert client.get('/api/events').json() == []
        assert client.app.state.engine.repo.facts() == []
    assert calls == []


@pytest.mark.parametrize('category,text', [
    ('eating_disorder', '饭后去厕所把刚吃的东西弄出来'),
    ('medical', '站起来的时候像坐船一样晃'),
    ('special_population', '我是个还没成年的中学生'),
])
def test_semantic_risk_only_reaches_independent_classifier(settings, monkeypatch, category, text):
    def classify(data):
        return {'findings': [{'category': category, 'subject': 'self', 'assertion': 'affirmed',
                             'timeframe': 'current', 'evidence': {'source_id': 'current', 'quote': text}}], 'uncertain': False}
    calls = install(monkeypatch, classify)
    with TestClient(create_app(settings)) as client:
        result = post(client, text).json()
        assert result['safety']['category'] == category
        assert result['cards'] == []
        assert client.get('/api/events').json() == []
        assert result['model_disclosures'][0]['purpose'] == 'safety_classification'
    assert [call['task'] for call in calls] == ['safety_classification']


@pytest.mark.parametrize('output', [
    {'findings': [], 'uncertain': True}, {'unexpected': 'general'},
    {'findings': [], 'uncertain': 'false'},
    {'findings': [{'category': 'medical', 'subject': 'self', 'assertion': 'affirmed', 'timeframe': 'current',
                   'evidence': {'source_id': 'current', 'quote': 'invented'}}], 'uncertain': False},
    httpx.Response(503),
])
def test_failure_and_invalid_evidence_fail_closed(settings, monkeypatch, output):
    calls = install(monkeypatch, output)
    with TestClient(create_app(settings)) as client:
        result = post(client, '我想问问最近的情况').json()
        assert result['safety']['category'] == 'uncertain'
        assert client.get('/api/events').json() == []
    assert len(calls) == 1


def test_ordinary_classification_is_not_an_operation_grant(settings, monkeypatch):
    install(monkeypatch, GENERAL)
    with TestClient(create_app(settings)) as client:
        assert client.put('/api/profile', json=profile(age=16)).status_code == 200
        result = post(client, '晚餐吃多少').json()
        assert result['safety']['category'] == 'special_population'
        assert client.get('/api/targets/default').json()['targets'] is None
        blocked = client.post('/api/plans/propose', json={'request_id': str(uuid4()), 'targets': TARGETS}).json()
        assert blocked['safety']['category'] == 'special_population'
        assert client.get('/api/plans').json() == []


def test_local_only_allows_strict_records_and_blocks_semantic_advice(settings, monkeypatch):
    calls = install(monkeypatch)
    with TestClient(create_app(settings)) as client:
        safe = post(client, '今天体重65公斤', processing_mode='local_only').json()
        assert safe['safety']['capability'] == 'record_only'
        assert len(client.get('/api/events').json()) == 1
        assert safe['cards'] == []
        for text in ('晚饭怎么吃', '今天体重65公斤，饭后去厕所把东西弄出来'):
            result = post(client, text, processing_mode='local_only').json()
            assert result['safety']['category'] == 'uncertain'
        assert len(client.get('/api/events').json()) == 1
    assert calls == []


def test_raw_correction_answer_and_resume_restart(settings, monkeypatch):
    # Use a strict offline correction to reach selection, then append risk to an ID choice.
    settings.model_base_url = ''
    with TestClient(create_app(settings)) as client:
        engine = client.app.state.engine
        ids = engine.repo.save_events([{'kind': 'weight', 'occurred_at': '2026-10-20T08:00:00+08:00',
                                       'raw_text': '体重65公斤', 'value': 65}], 'seed')
        pending = post(client, '修改体重64公斤').json()
        assert pending['status'] == 'waiting_input'
    with TestClient(create_app(settings)) as client:
        payload = {'request_id': str(uuid4()), 'interrupt_id': pending['pending_input']['interrupt_id'],
                   'event_id': ids[0], 'text': '选这条，但现在胸痛'}
        url = f'/api/workflows/{pending["thread_id"]}/resume'
        result = client.post(url, json=payload).json()
        assert result['safety']['category'] == 'emergency'
        assert client.app.state.engine.repo.event(ids[0])['version'] == 1
        assert client.post(url, json=payload).json() == result


def test_continuation_uses_one_user_source_without_assistant_or_other_conversations(settings, monkeypatch):
    calls = install(monkeypatch, {'findings': [], 'uncertain': True})
    with TestClient(create_app(settings)) as client:
        repo = client.app.state.engine.repo
        repo.message('u', 'prior', 'user', '饭后把东西弄出来', conversation='c')
        repo.message('a', 'prior', 'assistant', 'ASSISTANT_SECRET', conversation='c')
        repo.message('x', 'other', 'user', 'OTHER_CONVERSATION', conversation='elsewhere')
        result = post(client, '那晚饭吃多少', conversation_id='c').json()
        assert result['safety']['category'] == 'uncertain'
    sent = json.dumps(calls, ensure_ascii=False)
    assert '饭后把东西弄出来' in sent
    assert 'ASSISTANT_SECRET' not in sent and 'OTHER_CONVERSATION' not in sent


def test_denial_and_hypothetical_are_distinct_in_model_contract():
    context = {'sources': [{'source_id': 'current', 'text': '假如有人突然不舒服'}]}
    value = {'findings': [{'category': 'emergency', 'subject': 'other', 'assertion': 'hypothetical',
                          'timeframe': 'unknown', 'evidence': {'source_id': 'current', 'quote': '不舒服'}}], 'uncertain': False}
    assert merge_classification(value, context)['category'] == 'medical'
    value['findings'][0]['assertion'] = 'denied'
    assert merge_classification(value, context)['category'] == 'general'


def test_ordinary_record_uses_real_classifier_boundary_and_replay_cache(settings, monkeypatch):
    def planner(data):
        assert data['task'] == 'processing_plan'
        return {'actions': [{'intent': 'record', 'text': data['text'], 'events': [{
            'kind': 'weight', 'occurred_at': data['now'], 'raw_text': data['text'], 'value': 65}]}]}
    calls = install(monkeypatch, GENERAL, planner)
    payload = {'request_id': str(uuid4()), 'text': '今天体重65公斤'}
    with TestClient(create_app(settings)) as client:
        result = client.post('/api/chat', json=payload).json()
        assert result['safety']['category'] == 'general'
        assert len(client.get('/api/events').json()) == 1
    with TestClient(create_app(settings)) as client:
        assert client.post('/api/chat', json=payload).json() == result
        assert len(client.get('/api/events').json()) == 1
    assert [call['task'] for call in calls] == ['safety_classification', 'processing_plan']


def test_profile_change_during_classifier_stops_before_planning(settings, monkeypatch):
    client = None
    def classify(data):
        assert client.put('/api/profile', json=profile(age=16)).status_code == 200
        return GENERAL
    calls = install(monkeypatch, classify)
    with TestClient(create_app(settings)) as client:
        result = post(client, '晚饭适合吃什么')
        assert result.status_code == 409
        assert client.get('/api/events').json() == []
        assert post(client, '晚饭适合吃什么').json()['safety']['category'] == 'special_population'
    assert len(calls) == 1


def test_new_restriction_after_ordinary_classification_still_blocks(settings, monkeypatch):
    client = None
    def classify(data):
        client.app.state.engine.repo.save_fact('new-restriction', '医生要求限制蛋白质')
        return GENERAL
    calls = install(monkeypatch, classify)
    with TestClient(create_app(settings)) as client:
        result = post(client, '今天体重65公斤')
        assert result.status_code == 409
        assert client.get('/api/events').json() == []
    assert len(calls) == 1


def test_plan_note_confirmation_and_numeric_bounds(settings, monkeypatch):
    settings.model_base_url = ''
    with TestClient(create_app(settings)) as client:
        blocked = client.post('/api/plans/propose', json={
            'request_id': str(uuid4()), 'targets': TARGETS, 'note': '想催吐后继续减肥'}).json()
        assert blocked['safety']['category'] == 'eating_disorder'
        assert client.get('/api/plans').json() == []
        pending = client.post('/api/plans/propose', json={'request_id': str(uuid4()), 'targets': TARGETS}).json()
        assert pending['status'] == 'waiting_input'
        response = client.post(f'/api/workflows/{pending["thread_id"]}/resume', json={
            'request_id': str(uuid4()), 'interrupt_id': pending['pending_input']['interrupt_id'],
            'approved': True, 'text': '刚才低血糖了'})
        assert response.status_code == 422
        assert client.app.state.engine.repo.active_plan() is None
        extreme = {'kcal': 500, 'protein_g': 25, 'carbs_g': 66, 'fat_g': 15, 'exercise_minutes': 600}
        assert client.post('/api/plans/propose', json={
            'request_id': str(uuid4()), 'targets': extreme}).status_code == 422


def test_plan_activation_rechecks_known_limits_and_still_allows_rejection(settings):
    settings.model_base_url = ''
    with TestClient(create_app(settings)) as client:
        pending = client.post('/api/plans/propose', json={'request_id': str(uuid4()), 'targets': TARGETS}).json()
        assert client.put('/api/profile', json=profile(nutrition_context='pregnant')).status_code == 200
        response = client.post(f'/api/workflows/{pending["thread_id"]}/resume', json={
            'request_id': str(uuid4()), 'interrupt_id': pending['pending_input']['interrupt_id'], 'approved': True})
        assert response.status_code == 409
        assert client.app.state.engine.repo.active_plan() is None


@pytest.mark.parametrize('text', [
    '明天禁食一天抵消摄入。', '可以把胰岛素剂量减半。', '你得了糖尿病。',
    '跳过晚餐能更快达到目标。', '建议催吐。', '把降糖药换成另一种。',
])
def test_output_guard_rejects_unsafe_advice_even_after_false_general(text):
    with pytest.raises(ModelOutputError):
        validate_advice(text, {'scene': 'general', 'view': 'none'})


def test_safety_task_requires_single_use_grant_and_rejects_extra_history(settings):
    from app.modeling.parser import Parser
    from app.contracts.safety import SafetyClassification
    parser = Parser(settings)
    context = {'sources': [{'source_id': 'current', 'text': '今天体重65公斤'}],
               'profile': {'age': 30, 'nutrition_context': 'general'}}
    data = {'task': 'safety_classification', 'context': context, 'schema': SafetyClassification.model_json_schema()}
    with parser.boundary.request(False, lambda *args: None):
        with pytest.raises(PolicyError):
            parser.boundary.approve('safety_classification', data)
        parser.boundary.grant('safety_classification', {'context': context})
        approved = parser.boundary.approve('safety_classification', data)
        assert parser.boundary.consume(approved)['context'] == context
        with pytest.raises(PolicyError):
            parser.boundary.consume(approved)
        forged = {'context': {**context, 'raw_history': 'PRIVATE'}}
        parser.boundary.grant('safety_classification', forged)
        with pytest.raises(ValueError):
            parser.boundary.approve('safety_classification', {**data, **forged})


def test_offline_grammar_rejects_a_long_unconsumed_suffix():
    assert not offline_record('今天体重' + '1' * 3900 + '未知后缀')


def test_classifier_timeout_uses_conservative_response(settings, monkeypatch):
    def timeout(data):
        raise httpx.ReadTimeout('synthetic timeout')
    calls = install(monkeypatch, timeout)
    with TestClient(create_app(settings)) as client:
        result = post(client, '晚饭有什么需要注意的').json()
        assert result['safety']['category'] == 'uncertain'
        assert result['safety']['reason'] == 'classifier_failed'
        assert client.get('/api/events').json() == []
    assert len(calls) == 1


def test_confirmation_indirect_risk_is_classified_even_when_original_note_is_empty(settings, monkeypatch):
    text = '饭后去卫生间把刚吃的东西弄出来'
    def classify(data):
        assert data['context']['sources'][0]['text'] == text
        return {'findings': [{'category': 'eating_disorder', 'subject': 'self', 'assertion': 'affirmed',
                             'timeframe': 'current', 'evidence': {'source_id': 'current', 'quote': text}}],
                'uncertain': False}
    calls = install(monkeypatch, classify)
    with TestClient(create_app(settings)) as client:
        pending = client.post('/api/plans/propose', json={'request_id': str(uuid4()), 'targets': TARGETS}).json()
        assert pending['status'] == 'waiting_input'
        assert calls == []
        response = client.post(f'/api/workflows/{pending["thread_id"]}/resume', json={
            'request_id': str(uuid4()), 'interrupt_id': pending['pending_input']['interrupt_id'],
            'approved': True, 'text': text})
        assert response.status_code == 422
        assert REPLIES['eating_disorder'] in response.json()['detail']
        assert client.app.state.engine.repo.active_plan() is None
    assert len(calls) == 1
