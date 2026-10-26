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
            'saved_record_ids': state.get('record_ids', []),
        }
        if pending:
            result['pending_input'] = {
                'interrupt_id': pending[0].id,
                **pending[0].value,
            }
            result['reply'] = pending[0].value['question']
        if state.get('safety'):
            result['safety'] = state['safety']
        return result

    def run(self, kind, request_id, request_data, initial, conversation='main'):
        started = time.perf_counter()
        with self.execution_locks.hold(('request', request_id)):
            request_fingerprint = fingerprint({'kind': kind, 'data': request_data})
            with self.lock:
                self.check_request(request_id)
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
                    if current['status'] in ('resuming', 'resume_failed'):
                        raise WorkflowConflict('该流程已有已接收的补充，请重试原补充请求或取消后重新提交。')
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
                self.check_request(payload['request_id'])
                receipt = self._resume_receipt(thread_id, payload, request_fingerprint)
                if receipt is not None:
                    return receipt
                workflow = self.repo.workflow(thread_id)
                if not workflow:
                    raise ValueError('此流程没有待回答问题，可能已取消或记录已删除')
                self.check_context_deadline(thread_id)
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
                    if workflow['status'] not in ('waiting_input', 'resuming', 'resume_failed'):
                        raise ValueError('此流程没有待回答问题，可能已取消或记录已删除')
                    pending = workflow['result']['pending_input']
                    if pending['interrupt_id'] != payload['interrupt_id']:
                        raise ValueError('待回答问题已变化，请刷新')
                    attempt = self.repo.attempt_for_interrupt(thread_id, payload['interrupt_id'])
                    by_request = self.repo.resume_attempt(payload['request_id'])
                    if by_request and (
                        by_request['thread_id'] != thread_id
                        or by_request['fingerprint'] != request_fingerprint
                    ):
                        raise ValueError('重复请求内容不一致')
                    if attempt and (
                        attempt['request_id'] != payload['request_id']
                        or attempt['fingerprint'] != request_fingerprint
                    ):
                        raise WorkflowConflict('上一条补充尚未处理完成，请重试原答案，或取消后重新提交。')
                    if pending['kind'] == 'plan_confirmation':
                        if type(payload.get('approved')) is not bool:
                            raise ValueError('需要接受或拒绝计划')
                        answer = {'approved': payload['approved'], 'text': payload.get('text') or ''}
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
                    snapshot = self.graph_for(workflow['kind']).get_state(config)
                    saved = snapshot.values
                    interrupt_ids = {
                        item.id for task in snapshot.tasks for item in task.interrupts
                    }
                    if not saved or (not attempt and payload['interrupt_id'] not in interrupt_ids):
                        raise WorkflowConflict('原问题的进度已变化，无法安全接受新答案，请取消后重新提交。')
                    # A retry may have already consumed the interrupt. Continue the
                    # failed node with its bound answer, instead of resuming again.
                    invocation = (
                        Command(resume=answer)
                        if payload['interrupt_id'] in interrupt_ids else None
                    )
                    local_only = (
                        payload.get('processing_mode') == 'local_only'
                        or saved.get('processing_mode') == 'local_only'
                    )
                    self.repo.begin_resume(
                        thread_id, payload, request_fingerprint, display, conversation,
                    )
                try:
                    with self.lock:
                        self.check_execution()
                        if local_only and saved.get('processing_mode') != 'local_only':
                            self.graph_for(workflow['kind']).update_state(
                                config, {'processing_mode': 'local_only'}
                            )
                    state, disclosures = self.invoke(
                        workflow['kind'], invocation, thread_id, local_only
                    )
                    with self.lock:
                        self.check_execution()
                        result = self.format_result(thread_id, state)
                        result['model_disclosures'] = disclosures
                        result['duration_ms'] = round((time.perf_counter() - started) * 1000)
                        self.repo.finish_resume(
                            thread_id, payload, request_fingerprint, result, conversation,
                        )
                        return result
                except Exception:
                    with self.lock:
                        try:
                            self.check_execution()
                        except WorkflowConflict:
                            pass  # Cancellation/deletion must never be undone by failure recovery.
                        else:
                            self.repo.fail_resume(payload['request_id'])
                    raise

    def _resume_receipt(self, thread_id, payload, request_fingerprint):
        self.check_request(payload['request_id'])
        receipt = self.repo.receipt(payload['request_id'])
        if not receipt:
            return None
        if receipt['fingerprint'] != request_fingerprint or receipt['thread_id'] != thread_id:
            raise ValueError('重复请求内容不一致')
        return receipt['result']

    def cancel(self, thread_id):
        with self.lock:
            workflow = self.repo.workflow(thread_id)
            if not workflow:
                raise ValueError('没有待取消的流程')
            is_plan = workflow['kind'] == 'plan'
        # Never wait for a workflow lock under engine.lock. Plan confirmation
        # owns this lock through terminal result/receipt commit. Chat cancellation
        # stays immediate, including while its model request is in flight.
        key = ('workflow', thread_id) if is_plan else None
        with self.execution_locks.hold(key, blocking=False) as acquired:
            if not acquired:
                raise WorkflowConflict('计划正在确认，请稍后刷新状态，尚未取消。')
            with self.lock:
                self.repo.cancel_workflow(thread_id)
                self.checkpointer.delete_thread(thread_id)
        return {'cancelled': True}

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

    def delete_event(self, event_id, preview_id=None, request_id=None):
        return self.deletions.delete('event', event_id, preview_id=preview_id, request_id=request_id)

    def delete_fact(self, fact_id, preview_id=None, request_id=None):
        return self.deletions.delete('fact', fact_id, preview_id=preview_id, request_id=request_id)

    def archive(self, reference=None):
        with self.lock:
            return archive(self.repo, reference, self.deletions)

    def prune_context(self, reference=None):
        with self.lock:
            moment = reference or clock.now()
            completed_before = (
                moment - timedelta(days=self.settings.context_completed_days)
            ).astimezone(timezone.utc).isoformat()
            pending_before = (
                moment - timedelta(days=self.settings.context_pending_days)
            ).astimezone(timezone.utc).isoformat()
            self.recover_context_expirations()
            rows = self.repo.expired_workflows(completed_before, pending_before, moment.astimezone(timezone.utc).isoformat())
            count = 0
            for row in rows:
                with self.execution_locks.hold(('workflow', row['id']), blocking=False) as acquired:
                    if not acquired:
                        continue
                    self.repo.expire_workflow(row['id'])
                    count += 1
            self.recover_context_expirations(completed_before)
            self.repo.prune_workflow_context([], completed_before)
            return count

    def recover_context_expirations(self, messages_before=None):
        cutoff = messages_before or (clock.now() - timedelta(days=self.settings.context_completed_days)).astimezone(timezone.utc).isoformat()
        pending = self.repo.conn.execute("SELECT thread_id FROM context_expirations WHERE state='pending'").fetchall()
        for row in pending:
            workflow = self.repo.workflow(row['thread_id'])
            self.checkpointer.delete_thread(row['thread_id'])
            self.repo.prune_workflow_context([{'id': row['thread_id'], 'kind': workflow['kind'] if workflow else 'chat'}], cutoff)
        if pending:
            self.cp_conn.commit()
            self.deletions._compact(self.cp_conn)
            self.repo.compact()
            with self.repo.conn:
                for row in pending:
                    self.repo.conn.execute("UPDATE context_expirations SET state='completed' WHERE thread_id=?", (row['thread_id'],))
