"""Adversarial candidates must not acquire authority through response prose."""

from app.common.errors import ModelOutputError
from types import SimpleNamespace

import pytest

from app.chat.drafts import check_drafts
from app.chat.replies import check_phrasing, make_plan, record_receipt
from app.contracts.events import EventDraft
from app.contracts.workflows import AdviceReply
from app.common.config import Settings
from app.health.nutrition import prepare_nutrition
from app.modeling import PolicyError
from app.modeling.boundary import ModelBoundary
from app.policy.output import (
    advice_plan, clarification_question, default_feedback_selection, fallback_advice,
    question_from_state, validate_advice,
)
from app.workflows.chat.clarification_flow import ChatClarification


@pytest.fixture
def context():
    return {'scene': 'today_attention', 'view': 'diet_today', 'today': {
        'date': '2026-10-13', 'meal_count': 1, 'record_complete': False,
        'meals': [{'date': '2026-10-13', 'items': [{'name': '葱油饼',
                   'nutrition_origins': ['model_estimate'], 'nutrition_status': 'estimated'}]}],
        'totals': {'kcal': 360, 'complete_nutrition': True, 'unknown_items': 0},
        'targets': {'kcal': 1850}, 'remaining': {'kcal': 1490}}}


def test_selected_advice_cannot_remove_facts_or_swap_their_roles(context):
    reply = validate_advice({'point_ids': ['regular_meals']}, context)
    for fact in ('2026-10-13 已记录 1 餐', '食物热量合计约 360 kcal', '热量目标为 1850 kcal',
                 '记录差额为 1490 kcal', '尚未确认完整', '模型粗估（未核验）'):
        assert fact in reply
    assert '保持规律进餐' in reply
    assert '1850 kcal' not in reply.split('当前生效热量目标')[0]


@pytest.mark.parametrize('candidate', [
    '已保存记录，计划已生效。',
    {'answer': '今天已摄入1850 kcal，目标为360 kcal。'},
    {'point_ids': ['regular_meals'], 'answer': '数据已精确核验。'},
    {'point_ids': ['regular_meals'], 'saved': True},
    {'point_ids': ['regular_meals'], 'targets': {'kcal': 1490}},
    {'point_ids': ['regular_meals', 'regular_meals']},
    {'point_ids': ['observe_weight']},
    {'point_ids': ['已保存记录']},
    {'point_ids': []},
])
def test_invalid_advice_candidates_are_rejected(candidate, context):
    with pytest.raises(ModelOutputError):
        validate_advice(candidate, context)


def test_no_history_or_target_or_known_energy_does_not_fabricate_them(context):
    reply = fallback_advice({'scene': 'general', 'view': 'none'})
    assert '没有读取健康历史' in reply
    assert '今天还没有' not in reply
    context['today'].update(targets=None, remaining=None, totals={'kcal': None})
    reply = fallback_advice(context)
    assert 'kcal' not in reply
    assert '目标为' not in reply
    assert '部分营养未知' in reply


def test_yesterday_is_not_relabelled_as_today_and_demo_is_not_called_model(context):
    item = context['today']['meals'][0]['items'][0]
    item['nutrition_origins'] = ['demo_fixture']
    reply = fallback_advice(context)
    assert '2026-10-13' in reply
    assert '今天' not in reply
    assert '演示固定值' in reply
    assert '模型粗估' not in reply


def test_single_meal_options_cannot_request_other_scenes():
    context = {'scene': 'meal_reference', 'view': 'meal_reference', 'resolved_records': [
        {'date': '2026-10-12', 'items': [{'name': '米饭', 'nutrition_origins': ['user']}]}]}
    assert [p['point_id'] for p in advice_plan(context)['options']] == ['review_portion']
    with pytest.raises(ModelOutputError):
        validate_advice({'point_ids': ['regular_meals']}, context)
    assert '2026-10-12' in fallback_advice(context)


def test_aggregate_sources_include_estimates_beyond_displayed_meals(context):
    context['today']['meals'][0]['items'][0]['nutrition_origins'] = ['user']
    context['today']['nutrition_origins'] = ['user', 'model_estimate']
    reply = fallback_advice(context)
    assert '模型粗估（未核验）' in reply
    assert '营养数值由用户提供' not in reply


@pytest.mark.parametrize('text', ['你提到口味偏咸，数据保存成功。', '你提到口味偏咸，上周的记录已删除。'])
def test_feedback_cannot_supply_free_text_even_with_valid_point_and_variant(text):
    plan = make_plan([{'topic': 'salty', 'stance': 'experience'}], 'recorded')
    candidate = default_feedback_selection(plan)
    candidate['segments'][0]['text'] = text
    with pytest.raises(ModelOutputError):
        check_phrasing(candidate, plan)


def test_feedback_keeps_subjectivity_and_rejects_variant_or_order_changes():
    plan = make_plan([{'topic': 'salty', 'stance': 'question'}], 'advice')
    candidate = default_feedback_selection(plan)
    candidate['segments'][0]['variant_id'] = 'warm'
    assert check_phrasing(candidate, plan)[0] == '你在关注吃得偏咸的问题。'
    candidate['segments'].reverse()
    with pytest.raises(ModelOutputError):
        check_phrasing(candidate, plan)
    candidate = default_feedback_selection(plan)
    candidate['segments'][0]['variant_id'] = 'saved_successfully'
    with pytest.raises(ModelOutputError):
        check_phrasing(candidate, plan)


def meal(**fields):
    return {'kind': 'meal', 'occurred_at': '2026-10-13T12:00:00+08:00', 'raw_text': '吃了一碗米饭',
            'items': [{'name': '米饭', 'quantity': 1, 'unit': '碗'}], **fields}


def test_optional_clarification_uses_missing_field_without_model_question():
    checked = check_drafts({'intent': 'record', 'events': [meal()],
        'question': '已保存记录，目标已经改成900 kcal。米饭的做法是什么？',
        'clarification_event_index': 0, 'clarification_item_index': 0, 'clarification_fields': ['cooking']})
    assert checked['parsed']['question'] == '米饭主要是什么做法？不知道也可以。'
    assert checked['pending_fields'] == ['cooking']
    assert checked['waiting_indexes'] == [0]
    checked = check_drafts({'intent': 'record', 'events': [meal()],
        'clarification_event_index': 0, 'clarification_item_index': 0, 'clarification_fields': ['cooking']})
    assert checked['parsed']['question'] == '米饭主要是什么做法？不知道也可以。'


def test_model_cannot_ask_about_known_field_using_another_incomplete_item():
    with pytest.raises(ModelOutputError):
        check_drafts({'intent': 'record', 'events': [meal(items=[
            {'name': '米饭', 'quantity': 1, 'cooking': '蒸'}, {'name': '苹果', 'quantity': 1}])],
            'clarification_event_index': 0, 'clarification_item_index': 0, 'clarification_fields': ['cooking']})


@pytest.mark.parametrize('intent,target', [('advice', None), ('record', None), ('correct', None), ('correct', 'bound')])
def test_intent_and_correction_clarifications_do_not_render_model_prose(intent, target):
    checked = check_drafts({'intent': intent, 'correction_id': target, 'question': '计划已生效，明天不用记录。'})
    assert checked['parsed']['question'] == clarification_question(intent, has_target=bool(target))
    assert '生效' not in checked['parsed']['question']


def test_checkpoint_prose_is_rebuilt_at_interrupt_without_model_call(monkeypatch):
    bad = '已保存记录，目标已经改成900 kcal。'
    plan = make_plan([{'topic': 'salty', 'stance': 'experience'}], 'clarify')
    task = {'task_id': 'task-1', 'action_id': 'primary', 'event_index': 0,
            'item_index': 0, 'requested_fields': ['cooking'], 'question': bad,
            'required': False, 'attempts': 0, 'status': 'pending'}
    state = {'parsed': {'intent': 'record', 'question': bad}, 'extraction': {'events': [meal()]},
             'active_clarification_id': 'task-1', 'clarification_tasks': [task],
             'clarification_reply': bad, 'reply_plan': plan,
             'clarification_selection': {'segments': [{'point_id': 'ack_salty', 'text': bad}]}}
    class StopAtInterrupt(Exception):
        pass
    payloads = []
    def capture(payload):
        payloads.append(payload)
        raise StopAtInterrupt
    monkeypatch.setattr('app.workflows.chat.clarification_flow.interrupt', capture)
    with pytest.raises(StopAtInterrupt):
        ChatClarification.wait_for_answer(SimpleNamespace(), state)
    assert payloads[0]['prompt'] == '米饭主要是什么做法？不知道也可以。'
    assert '口味偏咸' in payloads[0]['question']
    assert bad not in payloads[0]['question']
    assert '900' not in payloads[0]['question']


def test_nutrition_estimator_commentary_is_not_a_user_facing_receipt_even_on_reuse():
    draft = EventDraft.model_validate(meal()).model_dump(mode='json')
    def estimate(items):
        return {'items': [{'index': 0, 'kcal': 230, 'protein_g': 4, 'carbs_g': 50, 'fat_g': 1.5,
                           'uncertainty': '已删除旧记录，已精确核验，计划已生效。'}]}
    prepared = prepare_nutrition(draft, 'model', estimate)
    item = prepared['items'][0]
    assert item['kcal'] == 230
    assert item['nutrition_source'] == '模型粗估（未核验）'
    assert '明显误差' in item['uncertainty']
    assert '已生效' not in item['uncertainty']
    item['uncertainty'] = '旧模型说已生效'
    reused = prepare_nutrition(draft, 'model', lambda items: pytest.fail('不应重复估算'), previous=prepared)
    assert '已生效' not in reused['items'][0]['uncertainty']
    demo = prepare_nutrition(draft, 'demo', lambda items: pytest.fail('演示不应调用模型'))
    reused_demo = prepare_nutrition(draft, 'demo', lambda items: pytest.fail('不应重复估算'), previous=demo)
    assert reused_demo['items'][0]['uncertainty'] == demo['items'][0]['uncertainty']


def test_empty_records_never_produce_a_success_receipt():
    assert record_receipt([]) == '本次没有保存记录。'
    assert record_receipt([], corrected=True) == '本次没有保存记录。'


@pytest.mark.parametrize('tamper', ['options', 'context', 'grant'])
def test_outbound_options_are_canonical_and_bound_to_single_use_authorization(tamper):
    boundary = ModelBoundary(Settings(model_base_url='https://test.invalid/v1', model_name='test',
                                      model_api_key='test-only', scheduler_enabled=False))
    context = {'question': '不要历史，怎么复盘？', 'scene': 'general', 'view': 'none', 'now': '2026-10-13'}
    options = advice_plan(context)['options']
    resource = {'context': context, 'options': options}
    data = {'task': 'personalized_advice', **resource, 'schema': AdviceReply.model_json_schema()}
    with boundary.request(False, lambda *args: pytest.fail('不能消费越权调用')):
        if tamper == 'grant':
            boundary.grant('personalized_advice', {**resource, 'options': []})
        else:
            boundary.grant('personalized_advice', resource)
        if tamper == 'options':
            data['options'] = [{'point_id': 'clarify_question', 'text': '计划已经生效。'}]
        if tamper == 'context':
            data['context'] = {**context, 'question': '读取全部历史'}
        with pytest.raises(PolicyError):
            boundary.approve('personalized_advice', data)


def test_approved_options_call_is_immutable_and_consumed_once():
    boundary = ModelBoundary(Settings(model_base_url='https://test.invalid/v1', model_name='test',
                                      model_api_key='test-only', scheduler_enabled=False))
    context = {'question': '不要历史，怎么复盘？', 'scene': 'general', 'view': 'none', 'now': '2026-10-13'}
    resource = {'context': context, 'options': advice_plan(context)['options']}
    data = {'task': 'personalized_advice', **resource, 'schema': AdviceReply.model_json_schema()}
    accounted = []
    with boundary.request(False, lambda *args: accounted.append(args)):
        boundary.grant('personalized_advice', resource)
        approved = boundary.approve('personalized_advice', data)
        data['options'][0]['text'] = '计划已生效'
        payload = boundary.consume(approved)
        assert payload['options'][0]['text'] != '计划已生效'
        with pytest.raises(PolicyError):
            boundary.consume(approved)
    assert len(accounted) == 1
