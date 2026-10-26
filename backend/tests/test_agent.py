from datetime import datetime, date, timedelta
from uuid import uuid4
import json

import pytest
from fastapi.testclient import TestClient

from app.common.config import Settings
from app.main import create_app
from app.workflows.engine import Engine
from app.common.clock import SHANGHAI, now
from app.contracts.events import EventDraft, MealItem
from app.contracts.profile import Targets
from app.health import analyze, archive, dashboard
from app.jobs.scheduler import tick


def uid():
    return str(uuid4())


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, scheduler_enabled=False)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


def chat(client, text, **kwargs):
    result = client.post('/api/chat', json={'request_id': uid(), 'text': text, **kwargs})
    assert result.status_code == 200, result.text
    return result.json()


def resume(client, result, **kwargs):
    response = client.post(f'/api/workflows/{result["thread_id"]}/resume', json={
        'request_id': uid(), 'interrupt_id': result['pending_input']['interrupt_id'], **kwargs,
    })
    assert response.status_code == 200, response.text
    return response.json()


TARGETS = {'kcal': 1800, 'protein_g': 100, 'carbs_g': 215, 'fat_g': 60, 'exercise_minutes': 120}


def propose(client, targets=None):
    response = client.post('/api/plans/propose', json={'request_id': uid(), 'targets': targets})
    assert response.status_code == 200, response.text
    return response.json()


def test_record_correction_and_idempotency(client):
    request = {'request_id': uid(), 'text': '中午吃了两碗牛肉面，加了一个卤蛋'}
    one = client.post('/api/chat', json=request)
    assert one.status_code == 200, one.text
    first = client.get('/api/dashboard').json()['totals']['kcal']
    assert first is None  # Offline records do not receive unverified demo estimates.
    assert client.post('/api/chat', json=request).json() == one.json()
    assert len(client.get('/api/events').json()) == 1
    corrected = chat(client, '不是两碗，是一碗')
    assert corrected['status'] == 'completed'
    records = client.get('/api/events').json()
    assert records[0]['version'] == 2
    assert records[0]['data']['items'][0]['quantity'] == 1
    assert len(records) == 1
    assert client.get('/api/dashboard').json()['totals']['kcal'] is None
    collision = client.post('/api/chat', json={**request, 'text': '今天体重 65 公斤'})
    assert collision.status_code == 409


def test_clarification_survives_restart(settings):
    with TestClient(create_app(settings)) as c:
        result = chat(c, '中午吃了炸鸡')
        assert result['status'] == 'waiting_input'
        assert c.get('/api/events').json() == []
    with TestClient(create_app(settings)) as c:
        assert c.get('/api/workflows').json()[0]['thread_id'] == result['thread_id']
        done = resume(c, result, text='一小份')
        assert done['status'] == 'completed'
        assert c.get('/api/events').json()[0]['data']['items'][0]['quantity'] == 1


def test_independent_records_saved_while_waiting(client):
    result = chat(client, '今天早上体重65公斤，今天跑步30分钟')
    assert result['status'] == 'waiting_input'
    records = client.get('/api/events').json()
    assert [r['kind'] for r in records] == ['weight']
    done = resume(client, result, text='很累')
    assert done['status'] == 'completed'
    records = client.get('/api/events').json()
    assert len(records) == 2
    assert next(r for r in records if r['kind'] == 'exercise')['data']['intensity'] == 'high'


def test_dates_and_unknown_nutrition(client):
    chat(client, '昨天体重66公斤，今天早上65公斤，昨晚睡了6小时')
    records = client.get('/api/events').json()
    yesterday = (now().date() - timedelta(days=1)).isoformat()
    assert len(records) == 3
    assert next(r for r in records if r['kind'] == 'sleep')['day'] == yesterday
    result = chat(client, '午餐吃了神秘炖菜')
    assert result['safety']['category'] == 'uncertain'  # Not in the offline full-string grammar.
    d = client.get('/api/dashboard').json()
    assert d['totals']['kcal'] is None
    assert d['totals']['unknown_items'] == 0
    assert not d['totals']['complete_nutrition']


def test_user_provided_nutrition_is_not_overwritten(client):
    chat(client, '午餐喝了一杯牛奶，热量120kcal，蛋白质8克，碳水12克，脂肪4克')
    d = client.get('/api/dashboard').json()
    assert d['totals']['kcal'] == 120
    assert d['totals']['protein_g'] == 8
    assert d['totals']['complete_nutrition']


def test_initial_plan_rejection_and_activation(client):
    p = propose(client, TARGETS)
    assert p['status'] == 'waiting_input'
    assert client.get('/api/dashboard').json()['targets'] is None
    resume(client, p, approved=False)
    assert client.get('/api/dashboard').json()['targets'] is None
    p = propose(client, TARGETS)
    resume(client, p, approved=True)
    assert client.get('/api/dashboard').json()['targets']['kcal'] == 1800
    assert len(next(p for p in client.get('/api/plans').json() if p['status'] == 'active')['data']['schedule']) == 7


def test_stale_proposal_does_not_activate(client):
    p = propose(client, TARGETS)
    chat(client, '今天体重65公斤')
    done = resume(client, p, approved=True)
    assert '过期' in done['reply']
    assert client.get('/api/dashboard').json()['targets'] is None


def test_cumulative_changes_require_confirmation(client):
    resume(client, propose(client, TARGETS), approved=True)
    first = {**TARGETS, 'kcal': 1850, 'carbs_g': 221}
    assert propose(client, first)['status'] == 'completed'
    # Each step is below 5%, but cumulative energy change from the last confirmation is over 5%.
    second = {**first, 'kcal': 1900, 'carbs_g': 227}
    assert propose(client, second)['status'] == 'waiting_input'
    assert client.get('/api/dashboard').json()['targets']['kcal'] == 1850


def test_resume_idempotency_and_validation(client):
    p = propose(client, TARGETS)
    payload = {'request_id': uid(), 'interrupt_id': p['pending_input']['interrupt_id'], 'approved': True}
    path = f'/api/workflows/{p["thread_id"]}/resume'
    first = client.post(path, json=payload)
    assert first.status_code == 200
    assert client.post(path, json=payload).json() == first.json()
    assert client.post(path, json={**payload, 'approved': False}).status_code == 409
    invalid = propose(client, {**TARGETS, 'kcal': 2000})
    path = f'/api/workflows/{invalid["thread_id"]}/resume'
    assert client.post(path, json={'request_id': uid(), 'interrupt_id': invalid['pending_input']['interrupt_id'], 'approved': 'true'}).status_code == 422


def test_deletion_clears_copies_and_checkpoints(client):
    chat(client, '中午吃了一碗牛肉面')
    p = propose(client, TARGETS)
    resume(client, p, approved=True)
    event_id = client.get('/api/events').json()[0]['id']
    assert client.delete(f'/api/events/{event_id}').status_code == 200
    assert client.get('/api/messages').json() == []
    assert client.get('/api/events').json() == []
    assert client.get('/api/workflows').json() == []
    export = client.get('/api/data/export').json()
    assert '牛肉面' not in json.dumps(export, ensure_ascii=False)
    engine = client.app.state.engine
    assert list(engine.checkpointer.list(None)) == []
    assert engine.repo.conn.execute('SELECT COUNT(*) FROM revisions').fetchone()[0] == 0


def seed(repo, day, sleep, kcal, index):
    repo.save_events([
        {'kind': 'sleep', 'occurred_at': datetime.combine(day - timedelta(days=1), datetime.min.time(), tzinfo=SHANGHAI).isoformat(), 'raw_text': '测试睡眠', 'value': sleep},
        {'kind': 'meal', 'occurred_at': datetime.combine(day, datetime.min.time(), tzinfo=SHANGHAI).isoformat(), 'raw_text': '测试餐',
         'items': [{'name': '测试餐', 'kcal': kcal, 'protein_g': 50, 'carbs_g': 200, 'fat_g': 50}]},
    ], f'seed:{index}')


def test_insights_exclude_incomplete_days_and_rejected_conclusions(settings):
    engine = Engine(settings)
    try:
        repo = engine.repo
        end = now().date()
        for i in range(8):
            day = end - timedelta(days=8 - i)
            seed(repo, day, 360 if i % 2 else 480, 2100 if i % 2 else 1700, i)
        assert analyze(repo) == []
        for i in range(8):
            repo.mark_complete((end - timedelta(days=8 - i)).isoformat(), True)
        insights = analyze(repo)
        found = next(i for i in insights if i['template'] == 'sleep_intake')
        assert found['data']['groups'] == [4, 4]
        assert found['data']['confidence'] == '初步观察'
        repo.insight_feedback(found['id'], 'rejected')
        assert not any(i['template'] == 'sleep_intake' for i in analyze(repo))
    finally:
        engine.close()


def test_archive_summarizes_only_fully_expired_months(settings):
    engine = Engine(settings)
    try:
        seed(engine.repo, date(2024, 9, 20), 420, 1800, 1)
        seed(engine.repo, date(2024, 10, 1), 420, 1800, 2)
        months = engine.archive(date(2025, 10, 8))
        assert '2024-09' in months
        assert '2024-10' not in months
        assert all(e['day'] >= '2024-10-01' for e in engine.repo.events())
        summary = engine.repo.summaries()[0]
        assert summary['data']['meal_count'] == 1
        assert '测试餐' not in json.dumps(summary, ensure_ascii=False)
    finally:
        engine.close()


def test_ambiguous_correction_does_not_modify_anything(client):
    chat(client, '昨天中午吃了一碗牛肉面')
    chat(client, '昨天晚餐吃了一碗牛肉面')
    result = chat(client, '昨天的牛肉面改成两碗')
    assert result['status'] == 'waiting_input'
    assert all(e['version'] == 1 for e in client.get('/api/events').json())


def test_cancel_keeps_already_saved_independent_records(client):
    result = chat(client, '今天体重65公斤，跑步30分钟')
    assert client.delete(f'/api/workflows/{result["thread_id"]}').status_code == 200
    assert len(client.get('/api/events').json()) == 1
    assert client.get('/api/workflows').json() == []


def test_notifications_are_deduplicated(settings):
    engine = Engine(settings)
    try:
        engine.repo.update_profile({'age': 30})
        moment = datetime(2026, 10, 8, 14, 0, tzinfo=SHANGHAI)
        tick(engine, moment)
        notices = engine.repo.notifications()
        tick(engine, moment)
        assert len(engine.repo.notifications()) == len(notices)
        assert notices
    finally:
        engine.close()


def test_advice_question_is_not_recorded_as_a_meal(client):
    result = chat(client, '牛肉面适合吗？')
    assert result['status'] == 'completed'
    assert client.get('/api/events').json() == []


def test_preferences_require_valid_weights(client):
    preferences = client.get('/api/preferences').json()
    preferences['weights']['energy'] = 50
    assert client.put('/api/preferences', json=preferences).status_code == 422


def test_natural_language_history_has_date_and_type_filters(client):
    chat(client, '2026-01-02中午吃了一碗牛肉面')
    chat(client, '2026-01-03体重65公斤')
    result = chat(client, '查询2026-01-02的饮食记录')
    assert len(result['evidence_refs']) == 1
    assert result['evidence_refs'][0]['date'] == '2026-01-02'
    assert result['evidence_refs'][0]['kind'] == 'meal'


def test_different_meal_dates_are_not_merged(client):
    result = chat(client, '昨天中午吃了一碗牛肉面，今天中午吃了一碗米饭')
    records = client.get('/api/events').json()
    assert len(records) == 2
    assert len({e['day'] for e in records}) == 2
    assert all(e['occurred_at'].endswith('+00:00') for e in records)


def test_resume_after_commit_but_before_response_is_replay_safe(settings, monkeypatch):
    payload = None
    with TestClient(create_app(settings)) as client:
        paused = chat(client, '中午吃了炸鸡')
        payload = {'request_id': uid(), 'interrupt_id': paused['pending_input']['interrupt_id'], 'text': '一小份'}
        def fail_after_commit(*args):
            raise ValueError('模拟响应前崩溃')
        monkeypatch.setattr(client.app.state.engine, 'format_result', fail_after_commit)
        path = f'/api/workflows/{paused["thread_id"]}/resume'
        assert client.post(path, json=payload).status_code == 409
        assert len(client.get('/api/events').json()) == 1
    with TestClient(create_app(settings)) as client:
        response = client.post(path, json=payload)
        assert response.status_code == 200, response.text
        assert response.json()['status'] == 'completed'
        assert len(client.get('/api/events').json()) == 1


def test_weight_suggestion_requires_complete_personal_history(client):
    assert client.get('/api/preferences/suggestion').json() is None
    resume(client, propose(client, TARGETS), approved=True)
    repo = client.app.state.engine.repo
    for i in range(8):
        day = now().date() - timedelta(days=i)
        seed(repo, day, 420, 2400, i)
        repo.mark_complete(day.isoformat(), True)
    suggestion = client.get('/api/preferences/suggestion').json()
    assert suggestion['weights']['energy'] == 35
    assert suggestion['weights']['macros'] == 20
    assert client.get('/api/preferences').json()['weights']['energy'] == 30


def test_deleting_a_fact_purges_its_context(client):
    chat(client, '我不喜欢燕麦')
    fact = client.get('/api/facts').json()[0]
    response = client.delete(f'/api/facts/{fact["id"]}')
    assert response.status_code == 200, response.text
    assert client.get('/api/facts').json() == []
    assert client.get('/api/messages').json() == []


def test_completed_chat_context_is_pruned_but_facts_remain(client):
    chat(client, '中午吃了一碗米饭')
    engine = client.app.state.engine
    future = now() + timedelta(days=31)
    assert engine.prune_context(future) == 1
    assert len(engine.repo.events()) == 1
    assert engine.repo.messages() == []
    assert list(engine.checkpointer.list(None)) == []
