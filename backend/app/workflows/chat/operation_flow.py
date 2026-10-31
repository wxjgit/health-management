"""把获批计划转换成强类型命令，并通过受控工具执行器原子执行。"""

from datetime import date

from ...common.errors import ConflictError, ModelOutputError, WorkflowStateError
from ...contracts.facts import FactDraft
from ...contracts.workflows import PlannedAction
from ...tools.contracts import (
    CorrectEventCommand,
    QueryEventsCommand,
    SaveEventCommand,
    SaveFactCommand,
    validate_command,
)
from .component import ChatComponent


class ChatOperations(ChatComponent):
    """Chat 中唯一持有高权限 ToolExecutor 的组件。"""

    def __init__(self, dependencies, interpretation, executor, runtime):
        super().__init__(dependencies)
        self.interpretation = interpretation
        self.executor = executor
        self.runtime = runtime

    def prepare_commands(self, state):
        parsed = state['parsed']
        commands = []
        existing_results = list(state.get('operation_results', []))
        completed = {result.get('operation_id') for result in existing_results}
        intent = parsed['intent']
        if intent in ('record', 'correct'):
            for index in state.get('event_indexes', []):
                # 等待追问的草稿尚未形成可写入 PreparedEvent，恢复后重新生成命令。
                if str(index) not in state.get('prepared_events', {}):
                    continue
                operation_id = f'{state["thread_id"]}:event:{index}'
                common = {
                    'operation_id': operation_id,
                    'event_index': index,
                    'event': self.interpretation.prepared_record(state, index),
                    'status': (
                        'completed'
                        if operation_id in completed or index in state.get('saved_indexes', [])
                        else 'pending'
                    ),
                }
                if intent == 'correct':
                    bound = self.harness.resolve(state, None)
                    if not bound or bound['id'] != parsed.get('correction_id'):
                        raise ConflictError()
                    expected = state.get('correction_version')
                    if expected is None or bound['version'] != expected:
                        raise ConflictError()
                    command = CorrectEventCommand(
                        **common,
                        event_id=bound['id'],
                        expected_version=expected,
                    )
                else:
                    command = SaveEventCommand(**common)
                commands.append(command.model_dump(mode='json'))
        elif intent == 'query':
            commands.append(self._query_command(state, 'primary', parsed))
        elif intent == 'fact' and parsed.get('fact_text'):
            commands.append(self._fact_command(
                state, 'primary', parsed['fact_text'], parsed.get('fact_draft'), state['thread_id'],
            ))

        for index, raw in enumerate(state.get('extra_actions', [])):
            action = PlannedAction.model_validate(raw)
            action_id = f'extra:{index}'
            if action.intent == 'query':
                commands.append(self._query_command(state, action_id, action))
            elif action.intent == 'fact':
                if not action.fact_text or action.fact_text not in state['input_text']:
                    raise ModelOutputError()
                commands.append(self._fact_command(
                    state,
                    action_id,
                    action.fact_text,
                    action.fact_draft,
                    f'{state["thread_id"]}:fact:{index}',
                ))
        return {
            'tool_schema_version': 1,
            'operation_commands': commands,
            'operation_results': existing_results,
        }

    @staticmethod
    def _query_command(state, action_id, data):
        def value(name):
            return data.get(name) if isinstance(data, dict) else getattr(data, name)

        def day(value):
            return value.isoformat() if isinstance(value, date) else value

        return QueryEventsCommand(
            operation_id=f'{state["thread_id"]}:query:{action_id}',
            action_id=action_id,
            start=day(value('query_start')),
            end=day(value('query_end')),
            event_kind=value('query_kind'),
            search=value('query_search'),
        ).model_dump(mode='json')

    def _fact_command(self, state, action_id, text, draft, fact_id):
        return SaveFactCommand(
            operation_id=f'{state["thread_id"]}:fact-command:{action_id}',
            action_id=action_id,
            fact_id=fact_id,
            text=text,
            source_message_id=state.get('source_message_id'),
            context_text=state.get('source_text') or state['input_text'],
            peer_versions=self.repo.fact_versions(),
            draft=(
                draft if isinstance(draft, FactDraft)
                else FactDraft.model_validate(draft) if draft else None
            ),
        ).model_dump(mode='json')

    @staticmethod
    def route(state):
        if state['parsed'].get('question'):
            return 'prepare_clarification'
        if any(raw.get('status') == 'pending' for raw in state.get('operation_commands', [])):
            return 'execute_operations'
        return 'route_response'

    def save_ready(self, state):
        """追问前先原子保存已经完整、并且彼此独立的记录。"""
        if state['parsed']['intent'] != 'record' or not state['parsed'].get('question'):
            return {}
        return self._run_pending(
            state,
            lambda command: (
                isinstance(command, SaveEventCommand)
                and command.event_index not in state.get('waiting_indexes', [])
            ),
        )

    def execute(self, state):
        return self._run_pending(state, lambda command: True)

    def _run_pending(self, state, selected):
        if state.get('tool_schema_version') != 1:
            raise WorkflowStateError()
        command_rows = [dict(raw) for raw in state.get('operation_commands', [])]
        pending = []
        pending_indexes = []
        for index, raw in enumerate(command_rows):
            command = validate_command(raw)
            if command.status == 'pending' and selected(command):
                context = self.runtime.execution_context(state, command.operation_id)
                pending.append((context, command))
                pending_indexes.append(index)
        if not pending:
            return {}

        executed = self.executor.execute_many(pending)
        results = list(state.get('operation_results', []))
        record_ids = list(state.get('record_ids', []))
        saved_indexes = list(state.get('saved_indexes', []))
        fact_id = state.get('fact_id')
        wrote_records = False
        wrote_facts = False
        for row_index, (_, command), result in zip(pending_indexes, pending, executed):
            command_rows[row_index]['status'] = 'completed'
            dumped = result.model_dump(mode='json')
            results.append(dumped)
            if isinstance(command, (SaveEventCommand, CorrectEventCommand)):
                record_ids.extend(result.entity_refs)
                saved_indexes.append(command.event_index)
                wrote_records = True
            elif isinstance(command, SaveFactCommand):
                if command.action_id == 'primary':
                    fact_id = result.entity_refs[0]
                wrote_facts = True

        trace = list(state.get('process_trace', []))
        if wrote_records:
            trace.append({
                'title': '保存记录',
                'detail': f'已由本地程序写入或更新 {len(set(record_ids))} 条健康记录。',
                'source': 'local',
            })
        if wrote_facts:
            trace.append({
                'title': '保存长期信息',
                'detail': '已由本地程序保存用户明确表达的偏好或限制；冲突仍需用户选择。',
                'source': 'local',
            })
        update = {
            'operation_commands': command_rows,
            'operation_results': results,
            'record_ids': list(dict.fromkeys(record_ids)),
            'saved_indexes': list(dict.fromkeys(saved_indexes)),
            'process_trace': trace,
        }
        if fact_id:
            update['fact_id'] = fact_id
        if wrote_facts:
            update['safety_fingerprint'] = self.safety_context({**state, **update})[1]
        return update
