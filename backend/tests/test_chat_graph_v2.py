"""Architecture contracts for the modular Chat Graph v2."""

from uuid import uuid4

import pytest

from app.common.config import Settings
from app.workflows.chat.state import ChatState
from app.workflows.engine import Engine


USER_ID = '00000000-0000-4000-8000-000000000001'


@pytest.fixture
def engine(tmp_path):
    value = Engine(Settings(data_dir=tmp_path, scheduler_enabled=False), user_id=USER_ID)
    try:
        yield value
    finally:
        value.close()


def run_chat(engine, text):
    request_id = str(uuid4())
    initial = {'input_text': text, 'processing_mode': 'local_only'}
    return engine.run('chat', request_id, initial, initial)


def snapshot(engine, thread_id):
    return engine.chat_graph.get_state({
        'configurable': {'thread_id': thread_id},
    }).values


def test_graph_exposes_separate_business_departments(engine):
    nodes = set(engine.chat_graph.get_graph().nodes)
    assert {
        'safety_gate', 'parse_initial', 'validate_drafts',
        'prepare_clarification', 'clarify', 'prepare_operations',
        'execute_operations', 'query_response', 'record_response',
        'advice_response', 'fact_response', 'compose_response',
    } <= nodes
    assert 'respond' not in nodes
    assert 'persist' not in nodes
    assert 'fact' not in nodes


def test_chat_state_does_not_share_legacy_pending_fields():
    fields = ChatState.__annotations__
    assert 'clarification_tasks' in fields
    assert 'active_clarification_id' in fields
    assert 'operation_commands' in fields
    assert 'operation_results' in fields
    assert not {'pending_event_index', 'pending_fields', 'pending_required', 'pending_item_index'} & fields.keys()


def test_checkpoint_uses_clarification_task_contract(engine):
    result = run_chat(engine, '中午吃了炸鸡')
    assert result['status'] == 'waiting_input'
    state = snapshot(engine, result['thread_id'])
    task = state['clarification_tasks'][0]
    assert state['active_clarification_id'] == task['task_id']
    assert task['requested_fields'] == ['quantity']
    assert task['required'] is False
    assert not {'pending_event_index', 'pending_fields', 'pending_required', 'pending_item_index'} & state.keys()


def test_completed_write_has_command_and_result_receipt(engine):
    result = run_chat(engine, '今天体重65公斤')
    assert result['status'] == 'completed'
    state = snapshot(engine, result['thread_id'])
    assert [command['status'] for command in state['operation_commands']] == ['completed']
    assert state['operation_results'][0]['kind'] == 'save_event'
    assert state['operation_results'][0]['entity_refs'] == result['saved_record_ids']
