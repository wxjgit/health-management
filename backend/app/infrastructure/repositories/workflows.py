"""Workflow, message and idempotency receipt persistence."""

import json

from ...common.clock import stamp
from .common import dump


class WorkflowRepositoryMixin:
    def workflow(self, thread_id):
        row = self.conn.execute(
            'SELECT * FROM workflows WHERE id=?', (thread_id,)
        ).fetchone()
        return {**dict(row), 'result': json.loads(row['result'])} if row else None

    def workflow_by_request(self, request_id):
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

    def workflow_result(self, thread_id, result):
        with self.lock, self.conn:
            self.conn.execute(
                'UPDATE workflows SET status=?, result=? WHERE id=?',
                (result['status'], dump(result), thread_id),
            )

    def messages(self, conversation_id='main', limit=100):
        rows = self.conn.execute(
            'SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at DESC LIMIT ?',
            (conversation_id, limit),
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
            "SELECT evidence_refs FROM messages WHERE conversation_id=? AND role='assistant' "
            'AND thread_id!=? ORDER BY created_at DESC, rowid DESC LIMIT 1',
            (conversation_id, excluding_thread),
        ).fetchone()
        return (
            list(dict.fromkeys(ref['id'] for ref in json.loads(row[0])))[:5]
            if row else []
        )

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

    def receipt(self, request_id):
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

    def waiting_workflows(self):
        with self.lock:
            rows = self.conn.execute(
                "SELECT id FROM workflows WHERE status='waiting_input' ORDER BY created_at"
            ).fetchall()
            return [self.workflow(row['id'])['result'] for row in rows]

    def expired_workflows(self, completed_before, pending_before):
        with self.lock:
            rows = self.conn.execute(
                "SELECT id,kind FROM workflows "
                "WHERE (status!='waiting_input' AND created_at<?) "
                "OR (status='waiting_input' AND created_at<?)",
                (completed_before, pending_before),
            ).fetchall()
            return [dict(row) for row in rows]

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
