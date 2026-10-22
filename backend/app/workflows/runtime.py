"""Workflow invocation, idempotency, checkpoint and lifecycle operations."""

import hashlib
import time
from datetime import timedelta, timezone
from uuid import uuid4

from langgraph.types import Command

from ..common import clock
from ..health import archive
from ..infrastructure.repositories.common import dump


def fingerprint(value):
    return hashlib.sha256(dump(value).encode()).hexdigest()


class WorkflowRuntimeMixin:
    def invoke(self, kind, invocation, thread_id, local_only=False):
        with self.parser.boundary.request(
            local_only,
            lambda purpose, size: self.repo.account_model_call(thread_id, purpose, size),
        ) as scope:
            state = self.graph_for(kind).invoke(
                invocation, {'configurable': {'thread_id': thread_id}}
            )
            return state, scope.disclosures

    def graph_for(self, kind):
        return {
            'chat': self.chat_graph,
            'plan': self.plan_graph,
            'analysis': self.analysis_graph,
        }[kind]

    def format_result(self, thread_id, state):
        pending = state.get('__interrupt__')
        result = {
            'thread_id': thread_id,
            'status': 'waiting_input' if pending else 'completed',
            'processing_mode': (
                'local_only'
                if self.parser.boundary.local_only
                else state.get('processing_mode', 'model_allowed')
            ),
            'reply': state.get('reply', ''),
            'cards': state.get('cards', []),
            'evidence_refs': state.get('evidence_refs', []),
            'process_trace': state.get('process_trace', []),
        }
        if pending:
            result['pending_input'] = {
                'interrupt_id': pending[0].id,
                **pending[0].value,
            }
            result['reply'] = pending[0].value['question']
        return result

    def run(self, kind, request_id, request_data, initial, conversation='main'):
        with self.lock:
            started = time.perf_counter()
            request_fingerprint = fingerprint({'kind': kind, 'data': request_data})
            existing = self.repo.workflow_by_request(request_id)
            if existing:
                if existing['fingerprint'] != request_fingerprint:
                    raise ValueError('同一个 request_id 不能用于不同请求')
                if existing['status'] in ['completed', 'waiting_input']:
                    return existing['result']
                thread_id = existing['id']
                invocation = (
                    None
                    if self.graph_for(kind).get_state({
                        'configurable': {'thread_id': thread_id}
                    }).values
                    else self._initial_state(initial, request_id, thread_id, conversation)
                )
            else:
                thread_id = str(uuid4())
                self.repo.register_workflow(
                    thread_id, request_id, request_fingerprint, kind
                )
                invocation = self._initial_state(
                    initial, request_id, thread_id, conversation
                )
                if kind == 'chat':
                    self.repo.message(
                        request_id,
                        thread_id,
                        'user',
                        initial['input_text'],
                        conversation=conversation,
                    )
            try:
                saved = self.graph_for(kind).get_state({
                    'configurable': {'thread_id': thread_id}
                }).values
                local_only = (
                    initial.get('processing_mode') == 'local_only'
                    or saved.get('processing_mode') == 'local_only'
                )
                state, disclosures = self.invoke(
                    kind, invocation, thread_id, local_only
                )
                result = self.format_result(thread_id, state)
                result['model_disclosures'] = disclosures
                result['duration_ms'] = round(
                    (time.perf_counter() - started) * 1000
                )
                self.repo.workflow_result(thread_id, result)
                self.repo.message(
                    thread_id + ':reply',
                    thread_id,
                    'assistant',
                    result['reply'],
                    result['cards'],
                    result['evidence_refs'],
                    conversation,
                    result['process_trace'],
                    result['duration_ms'],
                )
                return result
            except Exception:
                self.repo.workflow_result(thread_id, {
                    'thread_id': thread_id,
                    'status': 'failed',
                    'reply': '处理失败，可使用原请求重试。',
                })
                raise

    @staticmethod
    def _initial_state(initial, request_id, thread_id, conversation):
        return {
            **initial,
            'source_message_id': request_id,
            'source_text': initial.get('input_text', ''),
            'thread_id': thread_id,
            'conversation_id': conversation,
            'record_ids': [],
            'saved_indexes': [],
            'rounds': 0,
        }

    def resume(self, thread_id, payload):
        with self.lock:
            started = time.perf_counter()
            request_fingerprint = fingerprint(payload)
            receipt = self.repo.receipt(payload['request_id'])
            if receipt:
                if (
                    receipt['fingerprint'] != request_fingerprint
                    or receipt['thread_id'] != thread_id
                ):
                    raise ValueError('重复请求内容不一致')
                return receipt['result']
            workflow = self.repo.workflow(thread_id)
            if not workflow or workflow['status'] != 'waiting_input':
                raise ValueError('此流程没有待回答问题，可能已取消或记录已删除')
            pending = workflow['result']['pending_input']
            if pending['interrupt_id'] != payload['interrupt_id']:
                raise ValueError('待回答问题已变化，请刷新')
            if pending['kind'] == 'plan_confirmation':
                if type(payload.get('approved')) is not bool:
                    raise ValueError('需要接受或拒绝计划')
                answer = {'approved': payload['approved']}
                display = '接受计划' if payload['approved'] else '拒绝计划'
            else:
                if not payload.get('text'):
                    raise ValueError('请输入补充信息')
                answer = {
                    'text': payload['text'],
                    'event_id': payload.get('event_id'),
                    'source_message_id': payload['request_id'],
                }
                display = payload['text']
            config = {'configurable': {'thread_id': thread_id}}
            saved = self.graph_for(workflow['kind']).get_state(config).values
            local_only = (
                payload.get('processing_mode') == 'local_only'
                or saved.get('processing_mode') == 'local_only'
            )
            if local_only and saved.get('processing_mode') != 'local_only':
                self.graph_for(workflow['kind']).update_state(
                    config, {'processing_mode': 'local_only'}
                )
            conversation = saved.get('conversation_id', 'main')
            self.repo.message(
                payload['request_id'],
                thread_id,
                'user',
                display,
                conversation=conversation,
            )
            state, disclosures = self.invoke(
                workflow['kind'], Command(resume=answer), thread_id, local_only
            )
            result = self.format_result(thread_id, state)
            result['model_disclosures'] = disclosures
            result['duration_ms'] = round((time.perf_counter() - started) * 1000)
            self.repo.workflow_result(thread_id, result)
            self.repo.save_receipt(
                payload['request_id'], thread_id, request_fingerprint, result
            )
            self.repo.message(
                payload['request_id'] + ':reply',
                thread_id,
                'assistant',
                result['reply'],
                result['cards'],
                result['evidence_refs'],
                conversation=conversation,
                process_trace=result['process_trace'],
                duration_ms=result['duration_ms'],
            )
            return result

    def purge_checkpoints(self):
        threads = {
            item.config['configurable']['thread_id']
            for item in self.checkpointer.list(None)
        }
        for thread_id in threads:
            self.checkpointer.delete_thread(thread_id)
        self.cp_conn.commit()
        self.cp_conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        self.cp_conn.execute('VACUUM')
        self.repo.compact()

    def delete_event(self, event_id):
        with self.lock:
            self.repo.delete_event(event_id)
            self.purge_checkpoints()

    def archive(self, reference=None):
        with self.lock:
            months = archive(self.repo, reference)
            if months:
                self.purge_checkpoints()
            return months

    def prune_context(self, reference=None):
        with self.lock:
            moment = reference or clock.now()
            completed_before = (
                moment - timedelta(days=30)
            ).astimezone(timezone.utc).isoformat()
            pending_before = (
                moment - timedelta(days=366)
            ).astimezone(timezone.utc).isoformat()
            rows = self.repo.expired_workflows(completed_before, pending_before)
            for row in rows:
                self.checkpointer.delete_thread(row['id'])
            self.repo.prune_workflow_context(rows, completed_before)
            return len(rows)
