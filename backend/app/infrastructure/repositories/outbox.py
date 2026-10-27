"""Transactional hand-off from committed weekly review to an idempotent plan job."""
import json
from uuid import uuid4
from .common import dump
from .jobs import db_now
from ..unit_of_work import atomic
from ...jobs.context import LeaseLost


class OutboxRepositoryMixin:
    def stage_weekly_event(self, job, payload):
        from ...jobs.contracts import validate_payload
        validate_payload('auto_plan', payload)
        current = db_now(self.conn)
        self.conn.execute('''INSERT OR IGNORE INTO outbox_events
            (id,event_key,event_type,source_job_id,generation,payload,status,next_attempt_at,created_at)
            VALUES (?,?,'weekly_review_ready',?,?,?,'pending',?,?)''',
            (str(uuid4()), f"weekly-ready:{job['id']}", job['id'], job['generation'], dump(payload), current, current))

    def claim_outbox(self, lease_seconds=90):
        with atomic(self):
            current = db_now(self.conn)
            for row in self.conn.execute("SELECT * FROM outbox_events WHERE status='running' AND lease_until<=?", (current,)).fetchall():
                status = 'failed' if row['attempts'] >= 3 else 'pending'
                self.conn.execute('''UPDATE outbox_attempts SET finished_at=?,outcome='lease_expired',
                    error_category='runtime_timeout',error_code='job_lease_expired' WHERE event_id=? AND attempt_no=?''',
                    (current, row['id'], row['attempts']))
                self.conn.execute('UPDATE outbox_events SET status=?,lease_token=NULL,lease_until=NULL WHERE id=?', (status,row['id']))
            row = self.conn.execute("SELECT * FROM outbox_events WHERE status='pending' AND next_attempt_at<=? AND attempts<3 ORDER BY created_at,id LIMIT 1", (current,)).fetchone()
            if not row:
                return None
            token = uuid4().hex
            self.conn.execute("UPDATE outbox_events SET status='running',attempts=attempts+1,lease_token=?,lease_until=? WHERE id=?",
                              (token, current+int(lease_seconds*1000), row['id']))
            self.conn.execute('INSERT INTO outbox_attempts(event_id,attempt_no,started_at) VALUES (?,?,?)',
                              (row['id'],row['attempts']+1,current))
            return row['id'], token

    def consume_outbox(self, event_id, token):
        from datetime import datetime, timedelta, timezone
        from ...jobs.contracts import validate_payload
        with atomic(self):
            current = db_now(self.conn)
            row = self.conn.execute("SELECT * FROM outbox_events WHERE id=? AND status='running' AND lease_token=? AND lease_until>?",
                                    (event_id,token,current)).fetchone()
            if not row:
                raise LeaseLost()
            source = self.scheduled_job(row['source_job_id'])
            payload = json.loads(row['payload'])
            active = self.active_plan()
            cancelled = (not source or source['status'] != 'done' or source['generation'] != self.job_generation(source['logical_key']))
            cancelled = cancelled or bool(self.conn.execute("SELECT 1 FROM deletion_jobs WHERE state!='completed'").fetchone())
            if row['event_type'] != 'weekly_review_ready':
                raise ValueError('job_payload_unsupported')
            if not cancelled:
                validate_payload('auto_plan', payload)
                workflow = self.workflow(payload['source_workflow'])
                cancelled = not workflow or workflow['status'] != 'completed'
            if not cancelled and active and active['id'] == payload['base_plan_id']:
                end = datetime.fromisoformat(payload['period_end']).replace(tzinfo=timezone(timedelta(hours=8))) + timedelta(days=7)
                self.enqueue_job('auto-plan:' + source['logical_key'], 'auto_plan', payload, source['scheduled_at'],
                                 expires_at=int(end.timestamp()*1000), processing_mode=source['processing_mode'],
                                 generation=source['generation'])
            status = 'cancelled' if cancelled else 'consumed'
            self.conn.execute('UPDATE outbox_attempts SET finished_at=?,outcome=? WHERE event_id=? AND attempt_no=?',
                              (current,status,event_id,row['attempts']))
            self.conn.execute('''UPDATE outbox_events SET status=?,payload='{}',consumed_at=?,lease_token=NULL,lease_until=NULL
                WHERE id=? AND lease_token=?''', (status,current,event_id,token))

    def fail_outbox(self, event_id, token, permanent=False):
        with atomic(self):
            current = db_now(self.conn)
            row = self.conn.execute("SELECT * FROM outbox_events WHERE id=? AND status='running' AND lease_token=? AND lease_until>?",
                                    (event_id,token,current)).fetchone()
            if not row:
                return
            status = 'failed' if permanent or row['attempts']>=3 else 'pending'
            self.conn.execute("UPDATE outbox_attempts SET finished_at=?,outcome=?,error_code='outbox_delivery_failed' WHERE event_id=? AND attempt_no=?",
                              (current,status,event_id,row['attempts']))
            self.conn.execute("UPDATE outbox_events SET status=?,next_attempt_at=?,lease_token=NULL,lease_until=NULL,error_code='outbox_delivery_failed' WHERE id=?",
                              (status,current+60000,event_id))
