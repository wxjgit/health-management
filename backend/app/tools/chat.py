"""在固定授权、幂等和 UnitOfWork 边界内执行聊天业务命令。"""

import hashlib
import json

from ..common.errors import ConflictError, DomainValidationError
from ..infrastructure.unit_of_work import atomic
from .contracts import (
    CorrectEventCommand,
    EventMutationResult,
    EventSummary,
    QueryEventsCommand,
    QueryEventsResult,
    SaveEventCommand,
    SaveFactCommand,
    SaveFactResult,
    ToolExecutionContext,
)


CAPABILITY_BY_KIND = {
    'save_event': 'record',
    'correct_event': 'record',
    'query_events': 'query',
    'save_fact': 'fact',
}
ALLOWED_BY_GRANT = {
    'full': {'record', 'query', 'fact'},
    'record_only': {'record', 'query'},
    'preference_only': {'fact'},
}


def command_hash(command):
    payload = command.model_dump(mode='json', exclude={'status'})
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


class ChatToolExecutor:
    """聊天业务写入的唯一入口；Repository 只保存在这个高权限对象中。"""

    __slots__ = ('__repo',)

    def __init__(self, repo):
        self.__repo = repo

    def execute_many(self, authorized_commands):
        results = []
        with atomic(self.__repo):
            for context, command in authorized_commands:
                results.append(self._execute(context, command))
        return results

    def _execute(self, context: ToolExecutionContext, command):
        if context.operation_id != command.operation_id:
            raise ConflictError(code='request_payload_conflict')
        if self.__repo.workflow_generation(context.thread_id) != context.workflow_generation:
            raise ConflictError(code='workflow_context_changed')
        required = CAPABILITY_BY_KIND[command.kind]
        if required not in ALLOWED_BY_GRANT[context.granted_capability]:
            raise DomainValidationError(code='safety_policy_denied')

        request_hash = command_hash(command)
        metadata = {
            'request_hash': request_hash,
            'owner_id': context.user_id,
            'operation_kind': command.kind,
        }
        if isinstance(command, QueryEventsCommand):
            rows = self.__repo.events(
                command.start,
                command.end,
                command.event_kind,
                command.search,
                command.limit,
            )
            return QueryEventsResult(
                operation_id=command.operation_id,
                action_id=command.action_id,
                records=[EventSummary.model_validate({
                    key: row[key] for key in ('id', 'kind', 'day', 'raw_text')
                }) for row in rows],
                status='completed',
            )
        if isinstance(command, (SaveEventCommand, CorrectEventCommand)):
            saved = self.__repo.save_events(
                [command.event.model_dump(mode='json')],
                command.operation_id,
                command.event_id if isinstance(command, CorrectEventCommand) else None,
                expected_version=(
                    command.expected_version if isinstance(command, CorrectEventCommand) else None
                ),
                user_correction=isinstance(command, CorrectEventCommand),
                **metadata,
            )
            return EventMutationResult(
                operation_id=command.operation_id,
                action_id=command.action_id,
                kind=command.kind,
                entity_refs=saved,
                status='completed',
            )
        if isinstance(command, SaveFactCommand):
            fact = self.__repo.save_fact(
                command.fact_id,
                command.text,
                draft=command.draft.model_dump(mode='json') if command.draft else None,
                source_message_id=command.source_message_id,
                context_text=command.context_text,
                peer_versions=command.peer_versions,
                operation_id=command.operation_id,
                **metadata,
            )
            return SaveFactResult(
                operation_id=command.operation_id,
                action_id=command.action_id,
                entity_refs=[fact['id']],
                record={
                    key: fact.get(key)
                    for key in ('id', 'confirmation', 'lifecycle', 'valid_from')
                },
                status='completed',
            )
        raise RuntimeError('unsupported chat command')
