"""Authorized chat data operations and idempotent command execution."""

from ...common.errors import ConflictError, ModelOutputError
from ...contracts.workflows import PlannedAction
from .component import ChatComponent
from .contracts import OperationCommand, OperationResult


class ChatOperations(ChatComponent):
    """The only chat component allowed to read or write business records."""

    def __init__(self, engine, interpretation):
        super().__init__(engine)
        self.interpretation = interpretation

    def prepare_commands(self, state):
        parsed = state['parsed']
        commands = []
        existing_results = list(state.get('operation_results', []))
        completed = {result.get('operation_id') for result in existing_results}
        intent = parsed['intent']
        if intent in ('record', 'correct'):
            kind = 'correct_event' if intent == 'correct' else 'save_event'
            for index in state.get('event_indexes', []):
                operation_id = f'{state["thread_id"]}:event:{index}'
                commands.append(OperationCommand(
                    operation_id=operation_id,
                    kind=kind,
                    payload={'event_index': index},
                    required_capability='record',
                    status=(
                        'completed'
                        if operation_id in completed or index in state.get('saved_indexes', [])
                        else 'pending'
                    ),
                ).model_dump(mode='json'))
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
                commands.append(self._query_command(state, action_id, raw))
            elif action.intent == 'fact':
                if not action.fact_text or action.fact_text not in state['input_text']:
                    raise ModelOutputError()
                commands.append(self._fact_command(
                    state,
                    action_id,
                    action.fact_text,
                    action.fact_draft.model_dump(mode='json') if action.fact_draft else None,
                    f'{state["thread_id"]}:fact:{index}',
                ))
        return {'operation_commands': commands, 'operation_results': existing_results}

    @staticmethod
    def _query_command(state, action_id, data):
        def value(name):
            return data.get(name) if isinstance(data, dict) else getattr(data, name)

        return OperationCommand(
            operation_id=f'{state["thread_id"]}:query:{action_id}',
            action_id=action_id,
            kind='query_events',
            payload={
                'start': value('query_start'),
                'end': value('query_end'),
                'kind': value('query_kind'),
                'search': value('query_search'),
            },
            required_capability='query',
        ).model_dump(mode='json')

    @staticmethod
    def _fact_command(state, action_id, text, draft, fact_id):
        return OperationCommand(
            operation_id=f'{state["thread_id"]}:fact-command:{action_id}',
            action_id=action_id,
            kind='save_fact',
            payload={'fact_id': fact_id, 'text': text, 'draft': draft},
            required_capability='fact',
        ).model_dump(mode='json')

    @staticmethod
    def route(state):
        if state['parsed'].get('question'):
            return 'prepare_clarification'
        if any(raw.get('status') == 'pending' for raw in state.get('operation_commands', [])):
            return 'execute_operations'
        return 'route_response'

    def save_ready(self, state):
        """Commit independent complete records before another action is clarified."""
        if state['parsed']['intent'] != 'record' or not state['parsed'].get('question'):
            return {}
        commands = [dict(raw) for raw in state.get('operation_commands', [])]
        results = list(state.get('operation_results', []))
        ids = list(state.get('record_ids', []))
        indexes = list(state.get('saved_indexes', []))
        for command in commands:
            index = command.get('payload', {}).get('event_index')
            if (
                command['kind'] == 'save_event'
                and command['status'] == 'pending'
                and index not in state.get('waiting_indexes', [])
                and index not in indexes
            ):
                result, saved = self._execute_event(state, OperationCommand.model_validate(command))
                command['status'] = 'completed'
                results.append(result)
                ids.extend(saved)
                indexes.append(index)
        return {
            'operation_commands': commands,
            'operation_results': results,
            'record_ids': ids,
            'saved_indexes': indexes,
        }

    def execute(self, state):
        commands = [dict(raw) for raw in state.get('operation_commands', [])]
        results = list(state.get('operation_results', []))
        ids = list(state.get('record_ids', []))
        indexes = list(state.get('saved_indexes', []))
        fact_id = state.get('fact_id')
        wrote_records = False
        wrote_facts = False
        for command_data in commands:
            if command_data['status'] != 'pending':
                continue
            command = OperationCommand.model_validate(command_data)
            if command.kind in ('save_event', 'correct_event'):
                result, saved = self._execute_event(state, command)
                ids.extend(saved)
                indexes.append(command.payload['event_index'])
                wrote_records = True
            elif command.kind == 'query_events':
                rows = self.repo.events(
                    command.payload.get('start'),
                    command.payload.get('end'),
                    command.payload.get('kind'),
                    command.payload.get('search'),
                    limit=100,
                )
                result = OperationResult(
                    operation_id=command.operation_id,
                    action_id=command.action_id,
                    kind=command.kind,
                    records=rows,
                    status='completed',
                ).model_dump(mode='json')
            elif command.kind == 'save_fact':
                fact = self.repo.save_fact(
                    command.payload['fact_id'],
                    command.payload['text'],
                    draft=command.payload.get('draft'),
                    context_text=state.get('source_text') or state['input_text'],
                    source_message_id=state.get('source_message_id'),
                )
                fact_id = fact['id'] if command.action_id == 'primary' else fact_id
                result = OperationResult(
                    operation_id=command.operation_id,
                    action_id=command.action_id,
                    kind=command.kind,
                    entity_refs=[fact['id']],
                    records=[fact],
                    status='completed',
                ).model_dump(mode='json')
                wrote_facts = True
            else:
                raise RuntimeError('unsupported chat operation')
            command_data['status'] = 'completed'
            results.append(result)

        trace = list(state.get('process_trace', []))
        if wrote_records:
            trace.append({
                'title': '保存记录',
                'detail': f'已由本地程序写入或更新 {len(set(ids))} 条健康记录。',
                'source': 'local',
            })
        if wrote_facts:
            trace.append({
                'title': '保存长期信息',
                'detail': '已由本地程序保存用户明确表达的偏好或限制；冲突仍需用户选择。',
                'source': 'local',
            })
        update = {
            'operation_commands': commands,
            'operation_results': results,
            'record_ids': list(dict.fromkeys(ids)),
            'saved_indexes': list(dict.fromkeys(indexes)),
            'process_trace': trace,
        }
        if fact_id:
            update['fact_id'] = fact_id
        if wrote_facts:
            update['safety_fingerprint'] = self.safety_context({**state, **update})[1]
        return update

    def _execute_event(self, state, command):
        index = command.payload['event_index']
        parsed = state['parsed']
        if command.kind == 'correct_event':
            bound = self.harness.resolve(state, None)
            if not bound or bound['id'] != parsed.get('correction_id'):
                raise ConflictError()
            if (
                state.get('correction_version') is not None
                and bound['version'] != state['correction_version']
            ):
                raise ConflictError()
        validated = self.interpretation.prepared_record(state, index)
        saved = self.repo.save_events(
            [validated],
            command.operation_id,
            parsed.get('correction_id') if command.kind == 'correct_event' else None,
            expected_version=(
                state.get('correction_version') if command.kind == 'correct_event' else None
            ),
            user_correction=command.kind == 'correct_event',
        )
        result = OperationResult(
            operation_id=command.operation_id,
            action_id=command.action_id,
            kind=command.kind,
            entity_refs=saved,
            status='completed',
        ).model_dump(mode='json')
        return result, saved
