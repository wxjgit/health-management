"""Workflow invocation, idempotency, checkpoint and lifecycle operations."""

import hashlib
import time
from datetime import timedelta, timezone
from uuid import uuid4

from langgraph.types import Command

from ..common import clock
from ..health import archive
from ..infrastructure.repositories.common import dump
from .concurrency import WorkflowConflict


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
        started = time.perf_counter()
        with self.execution_locks.hold(('request', request_id)):
            request_fingerprint = fingerprint({'kind': kind, 'data': request_data})
            with self.lock:
                existing = self.repo.workflow_by_request(request_id)
                if existing:
                    if existing['fingerprint'] != request_fingerprint:
                        raise ValueError('同一个 request_id 不能用于不同请求')
                    thread_id = existing['id']
                else:
                    thread_id = str(uuid4())
                    self.repo.register_workflow(
                        thread_id, request_id, request_fingerprint, kind
                    )
                execution = self.capture_execution(thread_id)
            with self.serialized_execution(
                execution, conversation if kind == 'chat' else None
            ):
                with self.lock:
                    current = self.repo.workflow(thread_id)
                    if current['status'] in ['completed', 'waiting_input']:
                        return current['result']
                    saved = self.graph_for(kind).get_state({
                        'configurable': {'thread_id': thread_id}
                    }).values
                    invocation = (
                        None if saved else self._initial_state(
                            initial, request_id, thread_id, conversation
                        )
                    )
                    if kind == 'chat':
                        self.repo.message(
                            request_id, thread_id, 'user', initial['input_text'],
                            conversation=conversation,
                        )
                    local_only = (
                        initial.get('processing_mode') == 'local_only'
                        or saved.get('processing_mode') == 'local_only'
                    )
                try:
                    # No engine.lock here: guarded nodes/checkpoint operations
                    # protect local state, and model I/O releases the node lock.
                    state, disclosures = self.invoke(
                        kind, invocation, thread_id, local_only
                    )
                    with self.lock:
                        self.check_execution()
                        result = self.format_result(thread_id, state)
                        result['model_disclosures'] = disclosures
                        result['duration_ms'] = round(
                            (time.perf_counter() - started) * 1000
                        )
                        self.repo.workflow_result(thread_id, result)
                        self.repo.message(
                            thread_id + ':reply', thread_id, 'assistant',
                            result['reply'], result['cards'], result['evidence_refs'],
                            conversation, result['process_trace'], result['duration_ms'],
                        )
                        return result
                except Exception:
                    with self.lock:
                        try:
                            self.check_execution()
                        except WorkflowConflict:
                            # Cleanup/cancellation wins; never recreate erased data.
                            pass
                        else:
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
        started = time.perf_counter()
        with self.execution_locks.hold(('request', payload['request_id'])):
            request_fingerprint = fingerprint(payload)
            with self.lock:
                receipt = self._resume_receipt(thread_id, payload, request_fingerprint)
                if receipt is not None:
                    return receipt
                workflow = self.repo.workflow(thread_id)
                if not workflow:
                    raise ValueError('此流程没有待回答问题，可能已取消或记录已删除')
                config = {'configurable': {'thread_id': thread_id}}
                saved = self.graph_for(workflow['kind']).get_state(config).values
                conversation = saved.get('conversation_id', 'main')
                execution = self.capture_execution(thread_id)
            with self.serialized_execution(
                execution, conversation if workflow['kind'] == 'chat' else None
            ):
                with self.lock:
                    # Recheck after waiting for another execution of this workflow.
                    receipt = self._resume_receipt(thread_id, payload, request_fingerprint)
                    if receipt is not None:
                        return receipt
                    workflow = self.repo.workflow(thread_id)
                    if workflow['status'] != 'waiting_input':
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
                    saved = self.graph_for(workflow['kind']).get_state(config).values
                    local_only = (
                        payload.get('processing_mode') == 'local_only'
                        or saved.get('processing_mode') == 'local_only'
                    )
                    if local_only and saved.get('processing_mode') != 'local_only':
                        self.graph_for(workflow['kind']).update_state(
                            config, {'processing_mode': 'local_only'}
                        )
                    self.repo.message(
                        payload['request_id'], thread_id, 'user', display,
                        conversation=conversation,
                    )
                state, disclosures = self.invoke(
                    workflow['kind'], Command(resume=answer), thread_id, local_only
                )
                with self.lock:
                    self.check_execution()
                    result = self.format_result(thread_id, state)
                    result['model_disclosures'] = disclosures
                    result['duration_ms'] = round((time.perf_counter() - started) * 1000)
                    self.repo.workflow_result(thread_id, result)
                    self.repo.save_receipt(
                        payload['request_id'], thread_id, request_fingerprint, result
                    )
                    self.repo.message(
                        payload['request_id'] + ':reply', thread_id, 'assistant',
                        result['reply'], result['cards'], result['evidence_refs'],
                        conversation=conversation,
                        process_trace=result['process_trace'],
                        duration_ms=result['duration_ms'],
                    )
                    return result

    def _resume_receipt(self, thread_id, payload, request_fingerprint):
        receipt = self.repo.receipt(payload['request_id'])
        if not receipt:
            return None
        if receipt['fingerprint'] != request_fingerprint or receipt['thread_id'] != thread_id:
            raise ValueError('重复请求内容不一致')
        return receipt['result']

    def purge_checkpoints(self):
        # Invalidate in-flight nodes and background checkpoint writes before erase.
        self.context_epoch += 1
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
