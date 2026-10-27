"""Workflow, message and idempotency receipt persistence."""

from ...common.errors import ConflictError, WorkflowStateError

import json
from datetime import datetime, timedelta

from ...common.clock import stamp
from .common import dump


class WorkflowRepositoryMixin:
    def touch_workflow(self, thread_id):
        workflow = self.workflow(thread_id)
        if not workflow or workflow['status'] == 'expired':
            return
        policy = getattr(self, 'context_retention', {'pending': 7, 'plan': 7, 'completed': 30})
        pending = workflow['status'] in ('waiting_input', 'resuming', 'resume_failed', 'running')
        days = policy['plan' if pending and workflow['kind'] == 'plan' else 'pending' if pending else 'completed']
        moment = stamp()
        self.conn.execute('INSERT OR REPLACE INTO workflow_activity VALUES (?,?,?)',
                          (thread_id, moment, (datetime.fromisoformat(moment) + timedelta(days=days)).isoformat()))

    def workflow(self, thread_id):
        with self.lock:
            row = self.conn.execute(
                'SELECT * FROM workflows WHERE id=?', (thread_id,)
            ).fetchone()
            return {**dict(row), 'result': json.loads(row['result'])} if row else None

    def workflow_by_request(self, request_id):
        with self.lock:
            row = self.conn.execute(
                'SELECT id FROM workflows WHERE request_id=?', (request_id,)
            ).fetchone()
            return self.workflow(row['id']) if row else None

    def register_workflow(self, thread_id, request_id, fingerprint, kind):
        with self.lock, self.conn:
            self.conn.execute(
                'INSERT INTO workflows VALUES (?,?,?,?,?,?,?)',
                (thread_id, request_id, fingerprint, kind, 'running', '{}', stamp()),
            )
            self.persist_provenance('workflow', thread_id, independent=True, use_reads=False)
            self.conn.execute('INSERT OR IGNORE INTO workflow_generations VALUES (?,0)', (thread_id,))
            self.touch_workflow(thread_id)

    def workflow_result(self, thread_id, result):
        with self.lock, self.conn:
            result.update(self.dependency_metadata(self.workflow_node(thread_id)))
            self.conn.execute(
                'UPDATE workflows SET status=?, result=? WHERE id=?',
                (result['status'], dump(result), thread_id),
            )
            self.touch_workflow(thread_id)

    def messages(self, conversation_id='main', limit=100):
        with self.lock:
            rows = self.conn.execute(
                'SELECT * FROM messages WHERE conversation_id=? AND NOT EXISTS '
                '(SELECT 1 FROM workflow_activity a WHERE a.thread_id=messages.thread_id '
                'AND julianday(a.expires_at)<=julianday(?)) ORDER BY created_at DESC LIMIT ?',
                (conversation_id, stamp(), limit),
            ).fetchall()
            return [
                {
                    **dict(row),
                    'cards': json.loads(row['cards']),
                    'evidence_refs': json.loads(row['evidence_refs']),
                    'process_trace': json.loads(row['process_trace']),
                }
                for row in reversed(rows)
            ]

    def previous_reference_ids(self, conversation_id, excluding_thread):
        # Reference resolution reads only local result metadata, not chat text.
        row = self.conn.execute(
            "SELECT id,evidence_refs FROM messages WHERE conversation_id=? AND role='assistant' "
            'AND thread_id!=? ORDER BY created_at DESC, rowid DESC LIMIT 1',
            (conversation_id, excluding_thread),
        ).fetchone()
        self.observe_scope('references', conversation_id=conversation_id, excluding_thread=excluding_thread)
        if row:
            self.observe('message', row['id'])
        return (
            list(dict.fromkeys(ref['id'] for ref in json.loads(row['evidence_refs'])))[:5]
            if row else []
        )

    def safety_previous_input(self, conversation_id, excluding_thread):
        """One explicit continuation source; register reads for precise erasure."""
        row = self.conn.execute(
            "SELECT id,text FROM messages WHERE conversation_id=? AND role='user' "
            'AND thread_id!=? ORDER BY created_at DESC,rowid DESC LIMIT 1',
            (conversation_id, excluding_thread),
        ).fetchone()
        if row:
            self.observe('message', row['id'])
        return row['text'] if row else None

    def message(
        self,
        message_id,
        thread_id,
        role,
        text,
        cards=None,
        evidence=None,
        conversation='main',
        process_trace=None,
        duration_ms=0,
    ):
        with self.lock, self.conn:
            self._insert_message(
                message_id, thread_id, role, text, cards, evidence, conversation,
                process_trace, duration_ms,
            )

    def _insert_message(
        self, message_id, thread_id, role, text, cards=None, evidence=None,
        conversation='main', process_trace=None, duration_ms=0,
    ):
        """Insert inside the caller's transaction; never commit independently."""
        self.conn.execute(
            '''INSERT OR IGNORE INTO messages
            (id,thread_id,conversation_id,role,text,cards,evidence_refs,created_at,process_trace,duration_ms)
            VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (
                message_id,
                thread_id,
                conversation,
                role,
                text,
                dump(cards or []),
                dump(evidence or []),
                stamp(),
                dump(process_trace or []),
                max(0, int(duration_ms)),
            ),
        )
        workflow_node = self.workflow_node(thread_id)
        node = self.persist_provenance('message', message_id, sources=[workflow_node], use_reads=False)
        if role == 'user':
            self.link_source(node, message_id, 'text')
            self.link_source(workflow_node, message_id, 'input')

    def receipt(self, request_id):
        with self.lock:
            row = self.conn.execute(
                'SELECT * FROM receipts WHERE request_id=?', (request_id,)
            ).fetchone()
            return {**dict(row), 'result': json.loads(row['result'])} if row else None

    def save_receipt(self, request_id, thread_id, fingerprint, result):
        with self.lock, self.conn:
            self.conn.execute(
                'INSERT OR REPLACE INTO receipts VALUES (?,?,?,?)',
                (request_id, thread_id, fingerprint, dump(result)),
            )

    def resume_attempt(self, request_id):
        with self.lock:
            row = self.conn.execute(
                'SELECT * FROM resume_attempts WHERE request_id=?', (request_id,)
            ).fetchone()
            return {**dict(row), 'payload': json.loads(row['payload'])} if row else None

    def attempt_for_interrupt(self, thread_id, interrupt_id):
        with self.lock:
            row = self.conn.execute(
                'SELECT request_id FROM resume_attempts WHERE thread_id=? AND interrupt_id=?',
                (thread_id, interrupt_id),
            ).fetchone()
            return self.resume_attempt(row['request_id']) if row else None

    def _resume_status(self, thread_id, payload, status):
        workflow = self.workflow(thread_id)
        result = {
            **workflow['result'], 'status': status, 'retry_request': payload,
            'reply': ('已接收补充，正在处理。' if status == 'resuming' else
                      '上次补充未处理完成。请重试原答案，或取消后重新提交；已保存的独立记录保留。'),
        }
        self.conn.execute(
            'UPDATE workflows SET status=?,result=? WHERE id=?',
            (status, dump(result), thread_id),
        )

    def begin_resume(self, thread_id, payload, request_fingerprint, display, conversation):
        """Persist answer ownership before Graph can consume the interrupt."""
        with self.lock, self.conn:
            self.conn.execute(
                'INSERT OR IGNORE INTO resume_attempts VALUES (?,?,?,?,?,?,?)',
                (payload['request_id'], thread_id, payload['interrupt_id'],
                 request_fingerprint, dump(payload), 'executing', stamp()),
            )
            attempt = self.resume_attempt(payload['request_id'])
            if not attempt or attempt['thread_id'] != thread_id or attempt['fingerprint'] != request_fingerprint:
                raise ConflictError(code='request_payload_conflict')
            self.conn.execute(
                "UPDATE resume_attempts SET status='executing' WHERE request_id=?",
                (payload['request_id'],),
            )
            self._resume_status(thread_id, payload, 'resuming')
            self.touch_workflow(thread_id)
            self._insert_message(payload['request_id'], thread_id, 'user', display,
                                 conversation=conversation)

    def fail_resume(self, request_id):
        with self.lock, self.conn:
            attempt = self.resume_attempt(request_id)
            if not attempt:
                return
            workflow = self.workflow(attempt['thread_id'])
            if not workflow or workflow['status'] not in ('resuming', 'resume_failed'):
                return
            self.conn.execute(
                "UPDATE resume_attempts SET status='failed' WHERE request_id=?", (request_id,),
            )
            self._resume_status(attempt['thread_id'], attempt['payload'], 'resume_failed')

    def recover_resume_attempts(self):
        """Single-process startup: an interrupted execution is explicitly retryable."""
        with self.lock:
            ids = [row[0] for row in self.conn.execute(
                "SELECT request_id FROM resume_attempts WHERE status='executing'"
            ).fetchall()]
            for request_id in ids:
                self.fail_resume(request_id)

    def finish_resume(self, thread_id, payload, request_fingerprint, result, conversation):
        """Commit result, receipt, attempt and assistant reply in one transaction."""
        with self.lock, self.conn:
            result.update(self.dependency_metadata(self.workflow_node(thread_id)))
            self.conn.execute(
                'UPDATE workflows SET status=?,result=? WHERE id=?',
                (result['status'], dump(result), thread_id),
            )
            self.touch_workflow(thread_id)
            self.conn.execute(
                'INSERT INTO receipts VALUES (?,?,?,?)',
                (payload['request_id'], thread_id, request_fingerprint, dump(result)),
            )
            self.conn.execute(
                "UPDATE resume_attempts SET status='completed' WHERE request_id=?",
                (payload['request_id'],),
            )
            self._insert_message(
                payload['request_id'] + ':reply', thread_id, 'assistant', result['reply'],
                result['cards'], result['evidence_refs'], conversation,
                result['process_trace'], result['duration_ms'],
            )

    def cancel_workflow(self, thread_id):
        """Commit cancellation and rejection of a pending plan together."""
        with self.lock, self.conn:
            workflow = self.workflow(thread_id)
            if not workflow or workflow['status'] not in ('waiting_input', 'resuming', 'resume_failed'):
                raise WorkflowStateError()
            if workflow['kind'] == 'plan':
                plan = self.plan(thread_id)
                if not plan or plan['status'] != 'pending':
                    raise WorkflowStateError()
                self.conn.execute("UPDATE plans SET status='rejected' WHERE id=?", (thread_id,))
            result = {'thread_id': thread_id, 'status': 'cancelled',
                      'reply': '已取消待处理事项，已保存的独立记录保留。'}
            self.conn.execute(
                "UPDATE workflows SET status='cancelled',result=? WHERE id=?", (dump(result), thread_id),
            )
            self.touch_workflow(thread_id)
            self.conn.execute(
                "UPDATE resume_attempts SET status='cancelled' WHERE thread_id=? AND status!='completed'",
                (thread_id,),
            )

    def waiting_workflows(self):
        with self.lock:
            rows = self.conn.execute(
                "SELECT id FROM workflows WHERE status IN ('waiting_input','resuming','resume_failed') "
                'AND NOT EXISTS (SELECT 1 FROM workflow_activity a WHERE a.thread_id=workflows.id '
                'AND julianday(a.expires_at)<=julianday(?)) ORDER BY created_at', (stamp(),)
            ).fetchall()
            return [self.workflow(row['id'])['result'] for row in rows]

    def expired_workflows(self, completed_before, pending_before, reference=None):
        with self.lock:
            if reference:
                return [dict(row) for row in self.conn.execute(
                    'SELECT w.id,w.kind FROM workflows w LEFT JOIN workflow_activity a ON a.thread_id=w.id '
                    'WHERE (julianday(a.expires_at)<=julianday(?)) OR (a.thread_id IS NULL AND '
                    "((w.status NOT IN ('waiting_input','resuming','resume_failed','running') AND julianday(w.created_at)<julianday(?)) "
                    "OR (w.status IN ('waiting_input','resuming','resume_failed','running') AND julianday(w.created_at)<julianday(?))))",
                    (reference, completed_before, pending_before))]
            rows = self.conn.execute(
                "SELECT id,kind FROM workflows "
                "WHERE (status NOT IN ('waiting_input','resuming','resume_failed') AND created_at<?) "
                "OR (status IN ('waiting_input','resuming','resume_failed') AND created_at<?)",
                (completed_before, pending_before),
            ).fetchall()
            return [dict(row) for row in rows]

    def expire_workflow(self, thread_id):
        with self.lock, self.conn:
            workflow = self.workflow(thread_id)
            if not workflow:
                return
            requests = {workflow['request_id']}
            for table in ('receipts', 'resume_attempts'):
                requests.update(row[0] for row in self.conn.execute(
                    f'SELECT request_id FROM {table} WHERE thread_id=?', (thread_id,)))
            for request in requests:
                self.conn.execute('INSERT OR IGNORE INTO expired_requests VALUES (?,?,?)', (request, thread_id, stamp()))
            ids = workflow['result'].get('saved_record_ids', [])
            for row in self.conn.execute('SELECT result FROM operations WHERE key LIKE ?', (f'{thread_id}:event:%',)):
                ids.extend(json.loads(row[0]))
            self.conn.execute('INSERT OR IGNORE INTO context_expirations VALUES (?,?,?,?)',
                              (thread_id, dump(sorted(set(ids))), 'pending', stamp()))
            self.conn.execute("UPDATE workflows SET status='expired',result='{}' WHERE id=?", (thread_id,))
            self.conn.execute('UPDATE workflow_generations SET generation=generation+1 WHERE thread_id=?', (thread_id,))

    def expired_contexts(self, limit=20):
        return [{'thread_id': row['thread_id'], 'status': 'expired',
                 'reply': '此事项已过期，请重新开始；此前已保存的记录仍保留。',
                 'saved_record_ids': [event_id for event_id in json.loads(row['record_ids'])
                                      if self.conn.execute('SELECT 1 FROM events WHERE id=?', (event_id,)).fetchone()],
                 'cards': [], 'evidence_refs': [], 'process_trace': [], 'duration_ms': 0}
                for row in self.conn.execute('SELECT * FROM context_expirations ORDER BY created_at DESC LIMIT ?', (limit,))]

    def prune_workflow_context(self, workflows, messages_before):
        with self.lock, self.conn:
            for workflow in workflows:
                thread_id = workflow['id']
                self.conn.execute(
                    'DELETE FROM messages WHERE thread_id=?', (thread_id,)
                )
                self.conn.execute(
                    'DELETE FROM receipts WHERE thread_id=?', (thread_id,)
                )
                self.conn.execute('DELETE FROM resume_attempts WHERE thread_id=?', (thread_id,))
                self.conn.execute(
                    'DELETE FROM model_calls WHERE thread_id=?', (thread_id,)
                )
                self.conn.execute(
                    'DELETE FROM workflows WHERE id=?', (thread_id,)
                )
                if workflow['kind'] == 'plan':
                    self.conn.execute(
                        "UPDATE plans SET status='purged',data='{}' "
                        "WHERE id=? AND status='pending'",
                        (thread_id,),
                    )
            self.conn.execute(
                'DELETE FROM messages WHERE created_at<?', (messages_before,)
            )
