import json
from datetime import datetime
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.common.clock import SHANGHAI
from app.common.config import Settings
from app.evidence import (GroundingError, assert_grounded, ground_correction,
                          ground_event, ground_patch)
from app.main import create_app
from app.contracts.events import ExtractionEvent
from app.contracts.workflows import EventPatch


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    fixed = lambda: datetime(2026, 10, 21, 12, tzinfo=SHANGHAI)
    for module in ('app.common.clock', 'app.modeling.parser'):
        monkeypatch.setattr(module + '.now', fixed)


def test_model_value_is_replaced_by_local_normalization():
    event = ExtractionEvent(
        kind='exercise', occurred_at='2026-10-21T19:00:00+08:00',
        raw_text='今天走了半小时', exercise_type='跑步', value=60,
    )

    grounded = ground_event(event, '今天走了半小时')

    assert grounded.value == 30
    assert grounded.exercise_type == '步行'
    value = next(e for e in grounded.evidence if e.field == 'value')
    assert value.quote == '半小时'
    assert value.source == 'user_text'
    assert value.normalization.output_value == 30
    assert value.normalization.rule_version == 'duration-minute-v1'


def test_unbound_food_is_removed_from_draft():
    event = ExtractionEvent(
        kind='meal', occurred_at='2026-10-21T12:00:00+08:00',
        raw_text='今天吃了苹果', items=[{'name': '香蕉'}],
    )

    grounded = ground_event(event, '今天吃了苹果')

    assert grounded.items[0].name is None
    assert not any(e.field == 'item_name' for e in grounded.evidence)


def test_meal_time_and_slot_are_rebuilt_locally():
    event = ExtractionEvent(
        kind='meal', occurred_at='2026-10-21T05:17:00+08:00',
        raw_text='今天晚餐吃了苹果', meal_slot='breakfast', items=[{'name': '苹果'}],
    )

    grounded = ground_event(event, '今天晚餐吃了苹果')

    assert grounded.meal_slot == 'dinner'
    assert grounded.occurred_at.hour == 18
    assert any(e.field == 'meal_slot' and e.quote == '晚餐' for e in grounded.evidence)

    explicit = ground_event(event, '今天晚餐8点吃了苹果')
    assert explicit.occurred_at.hour == 20


def test_patch_ignores_model_value_and_uses_answer():
    patch = ground_patch(EventPatch(value=60), '半小时', 'sleep', ['value'])

    assert patch.value == 30
    assert patch.evidence[0].quote == '半小时'
    assert patch.evidence[0].normalization.output_unit == 'min'


def test_swapped_model_quantity_claims_cannot_swap_foods():
    text = '今天吃了100g苹果和200g香蕉'
    event = ExtractionEvent.model_validate({
        'kind': 'meal', 'occurred_at': '2026-10-21T12:00:00+08:00',
        'raw_text': text,
        'items': [
            {'name': '苹果', 'quantity': 100, 'unit': 'g'},
            {'name': '香蕉', 'quantity': 200, 'unit': 'g'},
        ],
        'evidence': [
            {'field': 'item_quantity', 'item_index': 0, 'quote': '200g',
             'start': 11, 'end': 15, 'source': 'user_text'},
            {'field': 'item_quantity', 'item_index': 1, 'quote': '100g',
             'start': 4, 'end': 8, 'source': 'user_text'},
        ],
    })

    grounded = ground_event(event, text, 'message-1')

    assert [(item.name, item.quantity) for item in grounded.items] == [('苹果', 100), ('香蕉', 200)]
    quantities = {e.item_index: e.quote for e in grounded.evidence if e.field == 'item_quantity'}
    assert quantities == {0: '100g', 1: '200g'}


def test_persistence_boundary_replays_span_and_current_value():
    grounded = ground_event(ExtractionEvent(
        kind='weight', occurred_at='2026-10-21T07:00:00+08:00',
        raw_text='今天体重65kg', value=65,
    ), '今天体重65kg', 'message-1').model_dump(mode='json')

    wrong_value = {**grounded, 'value': 80}
    with pytest.raises(GroundingError, match='当前字段值'):
        assert_grounded(wrong_value)

    wrong_span = json.loads(json.dumps(grounded))
    value_evidence = next(e for e in wrong_span['evidence'] if e['field'] == 'value')
    value_evidence['start'] += 1
    with pytest.raises(GroundingError, match='原文范围'):
        assert_grounded(wrong_span)


def test_correction_span_points_to_immutable_correction_message():
    original = ground_event(ExtractionEvent(
        kind='weight', occurred_at='2026-10-21T07:00:00+08:00',
        raw_text='今天体重65kg', value=65,
    ), '今天体重65kg', 'message-1').model_dump(mode='json')
    candidate = ExtractionEvent(
        kind='weight', occurred_at=original['occurred_at'],
        raw_text=original['raw_text'], value=70,
    )

    corrected = ground_correction(candidate, original, '改成70kg', 'message-2')
    evidence = next(e for e in corrected.evidence if e.field == 'value')
    source = next(s for s in corrected.evidence_sources if s.message_id == 'message-2')

    assert source.text[evidence.start:evidence.end] == evidence.quote == '70kg'
    assert corrected.raw_text != source.text


def test_model_user_nutrition_without_text_evidence_is_downgraded():
    grounded = ground_event(ExtractionEvent(
        kind='meal', occurred_at='2026-10-21T12:00:00+08:00',
        raw_text='今天吃了100g苹果',
        items=[{'name': '苹果', 'quantity': 100, 'unit': 'g',
                'kcal': 999, 'source': 'user'}],
    ), '今天吃了100g苹果', 'message-1')

    assert grounded.items[0].kcal is None
    assert grounded.items[0].source == 'unknown'
    assert not any(e.field == 'item_kcal' for e in grounded.evidence)


def test_legacy_record_is_preserved_but_not_upgraded_to_trusted():
    prior = {
        'kind': 'weight', 'occurred_at': '2026-10-21T07:00:00+08:00',
        'raw_text': '旧体重65kg', 'value': 65, 'items': [],
    }
    corrected = ground_correction(ExtractionEvent(
        kind='weight', occurred_at=prior['occurred_at'], raw_text=prior['raw_text'], value=65,
    ), prior, '补充备注', 'message-2').model_dump(mode='json')

    assert {e['source'] for e in corrected['evidence']} == {'unverified_legacy'}
    with pytest.raises(GroundingError):
        assert_grounded(corrected)
    assert assert_grounded(corrected, prior=prior).value == 65


def test_persistence_boundary_rejects_unverified_critical_value():
    record = {
        'kind': 'weight', 'occurred_at': '2026-10-21T07:00:00+08:00',
        'raw_text': '今天体重65kg', 'value': 65, 'items': [],
    }
    with pytest.raises(GroundingError):
        assert_grounded(record)


def test_chat_persists_locally_grounded_value_and_provenance(tmp_path, monkeypatch):
    settings = Settings(
        data_dir=tmp_path, model_base_url='https://model.test.invalid/v1',
        model_name='test-model', model_api_key='test-key', scheduler_enabled=False,
    )
    original = httpx.Client

    def handler(request):
        data = json.loads(json.loads(request.content)['messages'][1]['content'])
        assert data['task'] == 'processing_plan'
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({
            'actions': [{
                'intent': 'record', 'text': data['text'],
                'events': [{
                    'kind': 'weight', 'occurred_at': '2026-10-21T07:00:00+08:00',
                    'raw_text': data['text'], 'value': 80,
                }],
            }],
        }, ensure_ascii=False)}}]})

    monkeypatch.setattr(
        'app.modeling.parser.httpx.Client',
        lambda *a, **kw: original(*a, **kw, transport=httpx.MockTransport(handler)),
    )

    with TestClient(create_app(settings)) as client:
        response = client.post('/api/chat', json={
            'request_id': str(uuid4()), 'text': '今天体重65kg',
        })
        assert response.status_code == 200, response.text
        record = client.app.state.engine.repo.events()[0]['data']
        assert record['value'] == 65
        evidence = next(e for e in record['evidence'] if e['field'] == 'value')
        assert evidence['quote'] == '体重65kg'
        assert evidence['source'] == 'user_text'
        assert evidence['policy_version'] == 'health-grounding-v2'
