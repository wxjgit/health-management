"""验证 Chat Graph v2 的模块边界和状态契约。"""

from uuid import uuid4
from copy import deepcopy
import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.common.config import Settings
from app.common.errors import ConflictError
from app.health.facts import parse_fact
from app.tools.contracts import SaveFactCommand, ToolExecutionContext, validate_command
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
    assert state['tool_schema_version'] == 1
    assert state['operation_commands'][0]['schema_version'] == 1
    assert 'payload' not in state['operation_commands'][0]
    assert 'required_capability' not in state['operation_commands'][0]
    assert state['operation_results'][0]['kind'] == 'save_event'
    assert state['operation_results'][0]['schema_version'] == 1
    assert state['operation_results'][0]['entity_refs'] == result['saved_record_ids']


def execution_context(engine, state, operation_id):
    capability = state['safety'].get('capability')
    return ToolExecutionContext(
        user_id=engine.user_id,
        thread_id=state['thread_id'],
        operation_id=operation_id,
        workflow_generation=engine.repo.workflow_generation(state['thread_id']),
        granted_capability=(
            capability if capability in ('record_only', 'preference_only') else 'full'
        ),
        policy_version=state['safety']['policy_version'],
    )


def test_chat_components_receive_runtime_facades_not_engine(engine):
    assert not hasattr(engine.chat_workflow.interpretation, 'engine')
    assert not hasattr(engine.chat_reader, 'conn')
    assert not hasattr(engine.chat_reader, 'save_events')
    assert not hasattr(engine.chat_reader, 'save_fact')
    assert engine.harness.repo is engine.chat_reader


def test_operation_replay_requires_same_normalized_content(engine):
    result = run_chat(engine, '今天体重65公斤')
    state = snapshot(engine, result['thread_id'])
    raw = {**state['operation_commands'][0], 'status': 'pending'}
    command = validate_command(raw)
    context = execution_context(engine, state, command.operation_id)

    replay = engine.chat_tools.execute_many([(context, command)])[0]
    assert replay.entity_refs == result['saved_record_ids']

    changed = deepcopy(raw)
    changed['event']['value'] = 66.0
    changed_command = validate_command(changed)
    with pytest.raises(ConflictError) as caught:
        engine.chat_tools.execute_many([(context, changed_command)])
    assert caught.value.code == 'request_payload_conflict'
    assert len(engine.repo.events()) == 1


def test_handler_batch_rolls_back_business_write_and_receipt_together(engine):
    result = run_chat(engine, '今天体重65公斤')
    state = snapshot(engine, result['thread_id'])
    template = {**state['operation_commands'][0], 'status': 'pending'}
    before = len(engine.repo.events())
    operation_id = f'{state["thread_id"]}:rollback'
    first_raw = deepcopy(template)
    first_raw['operation_id'] = operation_id
    first_raw['event']['value'] = 66.0
    second_raw = deepcopy(first_raw)
    second_raw['event']['value'] = 67.0
    first = validate_command(first_raw)
    second = validate_command(second_raw)
    context = execution_context(engine, state, operation_id)

    with pytest.raises(ConflictError) as caught:
        engine.chat_tools.execute_many([(context, first), (context, second)])
    assert caught.value.code == 'request_payload_conflict'
    assert len(engine.repo.events()) == before
    assert engine.repo.conn.execute(
        'SELECT 1 FROM operations WHERE key=?', (operation_id,)
    ).fetchone() is None


def test_unknown_command_schema_is_rejected_before_write(engine):
    result = run_chat(engine, '今天体重65公斤')
    state = snapshot(engine, result['thread_id'])
    invalid = {**state['operation_commands'][0], 'schema_version': 2, 'status': 'pending'}
    before = len(engine.repo.events())
    with pytest.raises(ValidationError):
        validate_command(invalid)
    assert len(engine.repo.events()) == before


def test_fact_command_rejects_changed_peer_read_set(engine):
    result = run_chat(engine, '我不喜欢燕麦')
    state = snapshot(engine, result['thread_id'])
    text = '我喜欢苹果'
    draft = parse_fact(text).model_dump(
        mode='json', exclude={'content', 'source_kind', 'supersedes'}
    )
    operation_id = f'{state["thread_id"]}:peer-version-test'
    command = SaveFactCommand(
        operation_id=operation_id,
        fact_id=f'{state["thread_id"]}:new-fact',
        text=text,
        draft=draft,
        source_message_id='peer-version-source',
        context_text=text,
        peer_versions=engine.repo.fact_versions(),
    )
    context = execution_context(engine, state, operation_id)
    engine.repo.save_fact('concurrent-fact', '我喜欢香蕉')

    with pytest.raises(ConflictError) as caught:
        engine.chat_tools.execute_many([(context, command)])
    assert caught.value.code == 'resource_version_conflict'
    assert engine.repo.fact(command.fact_id) is None


def test_fact_replay_returns_the_original_typed_receipt(engine):
    result = run_chat(engine, '我喜欢燕麦')
    state = snapshot(engine, result['thread_id'])
    stored_result = state['operation_results'][0]
    command = validate_command({**state['operation_commands'][0], 'status': 'pending'})
    context = execution_context(engine, state, command.operation_id)
    fact = engine.repo.fact(state['fact_id'])
    engine.repo.decide_fact(fact['id'], 'retract', fact['version'])

    replay = engine.chat_tools.execute_many([(context, command)])[0].model_dump(mode='json')
    assert replay == stored_result


def test_query_result_is_a_minimal_summary_projection(engine):
    result = run_chat(engine, '今天体重65公斤')
    state = snapshot(engine, result['thread_id'])
    raw = {
        'schema_version': 1,
        'kind': 'query_events',
        'operation_id': f'{state["thread_id"]}:projection-query',
        'action_id': 'primary',
        'status': 'pending',
        'start': None,
        'end': None,
        'event_kind': None,
        'search': None,
        'limit': 100,
    }
    command = validate_command(raw)
    context = execution_context(engine, state, command.operation_id)
    projected = engine.chat_tools.execute_many([(context, command)])[0].model_dump(mode='json')
    assert set(projected['records'][0]) == {'id', 'kind', 'day', 'raw_text'}


def test_chat_layer_cannot_reach_engine_or_database_connection():
    chat_dir = Path(__file__).parents[1] / 'app' / 'workflows' / 'chat'
    violations = []
    for path in chat_dir.glob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [item.name for item in node.names]
                if any(name == 'sqlite3' or name.endswith('.repository') for name in names):
                    violations.append(f'{path.name}:{node.lineno}:import')
            if isinstance(node, ast.Attribute) and node.attr in ('engine', 'conn'):
                violations.append(f'{path.name}:{node.lineno}:{node.attr}')
    assert violations == []


def test_api_layer_does_not_access_health_repository_connection_directly():
    api_dir = Path(__file__).parents[1] / 'app' / 'api'
    violations = []
    for path in api_dir.glob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)) and any(
                item.name.endswith('.repositories.repository') for item in node.names
            ):
                violations.append(f'{path.name}:{node.lineno}:import')
            if (
                isinstance(node, ast.Attribute)
                and node.attr == 'conn'
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == 'repo'
            ):
                violations.append(f'{path.name}:{node.lineno}:conn')
    assert violations == []
