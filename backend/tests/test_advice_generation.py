import json
from datetime import datetime
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.clock import SHANGHAI
from app.config import Settings
from app.main import create_app
from model_helpers import planned_content


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    clock = lambda: datetime(2026, 10, 13, 12, tzinfo=SHANGHAI)
    for module in ['app.clock', 'app.parser', 'app.graphs', 'app.services', 'app.advice']:
        monkeypatch.setattr(module + '.now', clock)


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, model_base_url='https://test.invalid/v1',
                    model_name='qwen3.8-max', model_api_key='test-only-key', scheduler_enabled=False)


def install_model(monkeypatch, answer):
    calls = {'advice': []}
    original = httpx.Client

    def handler(request):
        body = json.loads(request.content)
        data = json.loads(body['messages'][1]['content'])
        if data.get('task') == 'personalized_advice':
            calls['advice'].append(data)
            content = {'answer': answer(data) if callable(answer) else answer}
        else:
            content = {'intent': 'advice'}
        content = planned_content(data, content)
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(content, ensure_ascii=False)}}]})

    monkeypatch.setattr('app.parser.httpx.Client',
                        lambda *args, **kwargs: original(*args, **kwargs, transport=httpx.MockTransport(handler)))
    return calls


def seed_today(repo):
    repo.save_events([{
        'kind': 'meal', 'occurred_at': '2026-10-13T18:00:00+08:00',
        'raw_text': '晚上吃了葱油饼，补充：100g', 'meal_slot': 'dinner', 'feedback': [],
        'items': [{
            'name': '葱油饼', 'quantity': 100, 'unit': 'g', 'size': None, 'cooking': None,
            'kcal': 360, 'protein_g': 8, 'carbs_g': 48, 'fat_g': 15,
            'nutrition_status': 'estimated', 'nutrition_source': '模型粗估（未核验）',
            'nutrition_reason': None, 'source': 'model_estimate',
        }],
    }], 'seed-advice-meal')
    repo.save_plan('active-plan', 'seed-plan', {
        'targets': {'kcal': 1850, 'protein_g': 92, 'carbs_g': 231,
                    'fat_g': 62, 'exercise_minutes': 150},
        'data_version': repo.version(), 'base_plan_id': None,
    })
    assert repo.decide_plan('active-plan', True) == 'active'


def ask(client, text, conversation_id='food-chat'):
    response = client.post('/api/chat', json={'request_id': str(uuid4()), 'conversation_id': conversation_id,
                                              'text': text})
    assert response.status_code == 200, response.text
    return response.json()


def test_advice_uses_structured_today_data_without_raw_dialogue(settings, monkeypatch):
    reply = ('按目前记录，你今天只记了100g葱油饼，还不能判断全天是否均衡。'
             '1490 kcal是相对目标的记录差额，不需要刻意吃满；下一餐可补充蛋白质食物和蔬菜。')
    calls = install_model(monkeypatch, reply)
    with TestClient(create_app(settings)) as client:
        repo = client.app.state.engine.repo
        seed_today(repo)
        repo.message('old-user', 'old-thread', 'user', '我刚吃了100g葱油饼', conversation='food-chat')
        repo.message('old-assistant', 'old-thread', 'assistant', '这是不应回传给模型的旧建议999 kcal',
                     conversation='food-chat')

        result = ask(client, '那我今天吃饭需要注意什么吗')

        assert result['reply'] == reply
        assert len(calls['advice']) == 1
        context = calls['advice'][0]['context']
        assert context['scene'] == 'today_attention'
        assert context['today']['remaining']['kcal'] == 1490
        assert context['today']['record_complete'] is False
        assert context['today']['meals'][0]['items'][0]['nutrition_status'] == 'estimated'
        assert 'recent_dialogue' not in context
        assert 'raw_text' not in json.dumps(context, ensure_ascii=False)
        assert '旧建议' not in json.dumps(context, ensure_ascii=False)
        assert result['evidence_refs'][0]['text'] == '100g葱油饼'
        assert any(item['title'] == '筛选建议上下文' for item in result['process_trace'])
        assert any(item['title'] == '生成个性化建议' and item['source'] == 'model'
                   for item in result['process_trace'])


def test_non_referential_question_does_not_send_raw_dialogue(settings, monkeypatch):
    calls = install_model(monkeypatch, '目前没有足够记录判断今天最需要调整的部分。')
    with TestClient(create_app(settings)) as client:
        repo = client.app.state.engine.repo
        repo.message('old-user', 'old-thread', 'user', '昨天聊了完全不同的话题', conversation='food-chat')
        ask(client, '今天吃饭需要注意什么吗')
        assert 'recent_dialogue' not in calls['advice'][0]['context']


@pytest.mark.parametrize('invalid', [
    '你今天摄入了999 kcal，下一餐按这个额度安排。',
    '你全天营养已经不足，需要补足剩余热量。',
])
def test_unverified_model_advice_falls_back_to_same_context(settings, monkeypatch, invalid):
    install_model(monkeypatch, invalid)
    with TestClient(create_app(settings)) as client:
        seed_today(client.app.state.engine.repo)
        result = ask(client, '那我今天吃饭需要注意什么吗')
        assert invalid not in result['reply']
        assert '葱油饼' in result['reply']
        assert '1490 kcal' in result['reply']
        assert '不需要为了吃满额度刻意补足' in result['reply']
        assert any(item['title'] == '生成个性化建议' and item['source'] == 'local'
                   for item in result['process_trace'])
