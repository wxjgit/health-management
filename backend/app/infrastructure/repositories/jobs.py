"""Durable task ownership. No caller may finish by logical key alone."""
import json
import random
from uuid import uuid4

from ...jobs.context import JobLease, LeaseLost
from ..unit_of_work import atomic
from .common import dump


def db_now(conn):
    return conn.execute("SELECT CAST((julianday('now')-2440587.5)*86400000 AS INTEGER)").fetchone()[0]


class JobsRepositoryMixin:
    def job_generation(self, key):
        if key.startswith('auto-plan:'):
            key = key[len('auto-plan:'):]
        row = self.conn.execute('SELECT generation FROM job_generations WHERE key=?', (key,)).fetchone()
        return row[0] if row else 0

    def scheduled_job(self, job_id):
        row = self.conn.execute('SELECT * FROM scheduled_jobs WHERE id=?', (job_id,)).fetchone()
        return {**dict(row), 'payload': json.loads(row['payload'])} if row else None

    def enqueue_job(self, key, kind, payload, scheduled_at, *, expires_at=None,
                    generation=None, processing_mode='local_only', max_attempts=3):
        from ...jobs.contracts import validate_payload
        validate_payload(kind, payload)
        if processing_mode not in ('local_only', 'model_allowed'):
            raise ValueError('job_processing_mode_invalid')
        with atomic(self):
            generation = self.job_generation(key) if generation is None else generation
            if generation != self.job_generation(key):
                raise LeaseLost()
            job_id = str(uuid4())
            self.conn.execute('''INSERT OR IGNORE INTO scheduled_jobs
                (id,logical_key,generation,kind,payload,processing_mode,status,max_attempts,
                 scheduled_at,next_attempt_at,expires_at,created_at)
                VALUES (?,?,?,?,?,?,'pending',?,?,?,?,?)''',
                (job_id, key, generation, kind, dump(payload), processing_mode, max_attempts,
                 scheduled_at, scheduled_at, expires_at, db_now(self.conn)))
            return self.conn.execute('SELECT id FROM scheduled_jobs WHERE logical_key=? AND generation=?',
                                     (key, generation)).fetchone()[0]

    def assert_lease(self, lease):
        # Called by the connection's write guard as well as explicit boundaries.
        import sqlite3
        row = sqlite3.Connection.execute(self.conn, '''SELECT 1 FROM scheduled_jobs j
            WHERE j.id=? AND j.generation=? AND j.lease_token=? AND j.status='running'
            AND j.lease_until>CAST((julianday('now')-2440587.5)*86400000 AS INTEGER)
            AND j.generation=COALESCE((SELECT generation FROM job_generations
                WHERE key=CASE WHEN j.kind='auto_plan' THEN substr(j.logical_key,11) ELSE j.logical_key END),0)
            AND (j.kind='archive' OR NOT EXISTS(SELECT 1 FROM deletion_jobs WHERE state='business_pending'))''',
            (lease.id, lease.generation, lease.token)).fetchone()
        if not row:
            raise LeaseLost()

    def recover_jobs(self, *, due_at=None):
        with atomic(self):
            current = db_now(self.conn)
            due_at = current if due_at is None else due_at
            rows = self.conn.execute("SELECT * FROM scheduled_jobs WHERE status IN ('pending','running')").fetchall()
            for row in rows:
                status = code = category = None
                if row['generation'] != self.job_generation(row['logical_key']):
                    status, code, category = 'cancelled', 'job_generation_changed', 'privacy_cancelled'
                elif row['expires_at'] is not None and row['expires_at'] <= due_at:
                    status, code, category = 'skipped', 'job_window_expired', 'obsolete'
                elif row['status'] == 'running' and row['lease_until'] <= current:
                    status = 'failed' if row['attempts'] >= row['max_attempts'] else 'pending'
                    code = 'job_attempts_exhausted' if status == 'failed' else 'job_lease_expired'
                    category = 'runtime_timeout'
                elif row['status'] == 'pending' and row['attempts'] >= row['max_attempts']:
                    status, code, category = 'failed', 'job_attempts_exhausted', 'runtime_timeout'
                if status:
                    if row['status'] == 'running' and category == 'runtime_timeout':
                        from ...observability.context import observe
                        observe('job_recoveries', job_kind=row['kind'], outcome='lease_lost')
                    self._end_attempt(row, current, code, category)
                    self.conn.execute('''UPDATE scheduled_jobs SET status=?,lease_token=NULL,lease_until=NULL,
                        next_attempt_at=?,finished_at=?,error_category=?,error_code=? WHERE id=?''',
                        (status, current, None if status == 'pending' else current, category, code, row['id']))

    def _end_attempt(self, row, current, code, category):
        self.conn.execute('''UPDATE job_attempts SET finished_at=?,outcome=?,error_category=?,error_code=?
            WHERE job_id=? AND attempt_no=? AND finished_at IS NULL''',
            (current, code, category, code, row['id'], row['attempts']))

    def claim_job(self, worker_id, lease_seconds=90, *, due_at=None):
        with atomic(self):
            current = db_now(self.conn)
            due_at = current if due_at is None else due_at
            self.recover_jobs(due_at=due_at)
            if self.conn.execute("SELECT 1 FROM deletion_jobs WHERE state!='completed' LIMIT 1").fetchone():
                return None
            row = self.conn.execute('''SELECT * FROM scheduled_jobs WHERE status='pending'
                AND next_attempt_at<=? AND attempts<max_attempts ORDER BY scheduled_at,id LIMIT 1''',
                (due_at,)).fetchone()
            if not row:
                return None
            token = uuid4().hex
            changed = self.conn.execute('''UPDATE scheduled_jobs SET status='running',attempts=attempts+1,
                lease_token=?,lease_until=?,worker_id=?,started_at=?,heartbeat_at=?,finished_at=NULL,
                error_category=NULL,error_code=NULL WHERE id=? AND status='pending' AND attempts=?''',
                (token, current + int(lease_seconds * 1000), worker_id, current, current, row['id'], row['attempts'])).rowcount
            if not changed:
                return None
            attempt = row['attempts'] + 1
            self.conn.execute('INSERT INTO job_attempts(job_id,attempt_no,started_at,heartbeat_at) VALUES (?,?,?,?)',
                              (row['id'], attempt, current, current))
            from ...observability.context import mark_job, observe
            mark_job(kind=row['kind'])
            observe('job_queue_wait_seconds', max(0, (current-row['next_attempt_at'])/1000), job_kind=row['kind'])
            return JobLease(row['id'], row['generation'], token, attempt)

    def heartbeat_job(self, lease, lease_seconds=90):
        with atomic(self):
            self.assert_lease(lease)
            current = db_now(self.conn)
            self.conn.execute('UPDATE scheduled_jobs SET heartbeat_at=?,lease_until=? WHERE id=?',
                              (current, current + int(lease_seconds * 1000), lease.id))
            self.conn.execute('UPDATE job_attempts SET heartbeat_at=? WHERE job_id=? AND attempt_no=?',
                              (current, lease.id, lease.attempt))

    def finish_scheduled_job(self, lease, status='done', *, outcome='completed', result_ref=None,
                             category=None, code=None, retry=False):
        if status not in ('done', 'failed', 'skipped', 'cancelled'):
            raise ValueError('job_finish_status_invalid')
        with atomic(self):
            self.assert_lease(lease)
            row = self.scheduled_job(lease.id)
            current = db_now(self.conn)
            next_time = current
            if retry and row['attempts'] < row['max_attempts']:
                status = 'pending'
                next_time += (60 if row['attempts'] == 1 else 300) * 1000 + random.randrange(5000)
            elif retry:
                code = 'job_attempts_exhausted'
            self._end_attempt(row, current, code or outcome, category)
            # Last write: invalidating ownership deliberately fences any further work in this scope.
            self.conn.execute('''UPDATE scheduled_jobs SET status=?,next_attempt_at=?,finished_at=?,
                result_ref=?,outcome_code=?,error_category=?,error_code=?,lease_token=NULL,lease_until=NULL
                WHERE id=? AND lease_token=?''',
                (status, next_time, None if status == 'pending' else current, result_ref,
                 outcome, category, code, lease.id, lease.token))
            from ...observability.context import mark_job
            mark_job(outcome='retry' if status == 'pending' else status)

    def cancel_jobs(self, ids, code='job_source_deleted'):
        current = db_now(self.conn)
        for job_id in ids:
            row = self.scheduled_job(job_id)
            if not row:
                continue
            self._end_attempt(row, current, code, 'privacy_cancelled')
            self.conn.execute('''UPDATE scheduled_jobs SET status='cancelled',payload='{}',result_ref=NULL,
                lease_token=NULL,lease_until=NULL,finished_at=?,error_category='privacy_cancelled',error_code=?
                WHERE id=?''', (current, code, job_id))
            self.conn.execute('''UPDATE outbox_events SET status='cancelled',payload='{}',lease_token=NULL,
                lease_until=NULL,error_category='privacy_cancelled',error_code=? WHERE source_job_id=?''', (code, job_id))

    def reset_scheduled_key(self, key):
        ids = [r[0] for r in self.conn.execute('SELECT id FROM scheduled_jobs WHERE logical_key=?', (key,))]
        # A derived plan has the same logical generation boundary as its source weekly job.
        ids += [r[0] for r in self.conn.execute('SELECT id FROM scheduled_jobs WHERE logical_key=?', ('auto-plan:' + key,))]
        self.cancel_jobs(ids)

    def jobs_for_deletion(self, items, threads, kind, object_id):
        from datetime import date, timedelta
        refs = {item['id'] for item in items} | set(threads)
        event = self.event(object_id) if kind == 'event' else None
        day = date.fromisoformat(event['day']) if event else None
        ids = []
        for row in self.conn.execute('SELECT * FROM scheduled_jobs').fetchall():
            payload = json.loads(row['payload'])
            linked = row['result_ref'] in refs or any(payload.get(k) in refs for k in ('source_workflow','base_plan_id'))
            # A running calculation may not have committed its result/lineage yet.
            if row['status'] == 'running' and row['kind'] != 'archive':
                end = date.fromisoformat(payload['period_end']) if payload.get('period_end') else None
                linked = linked or kind == 'fact' or (day is not None and end is not None and
                    end-timedelta(days=6 if row['kind'] in ('weekly','auto_plan') else 0) <= day <= end)
            if linked:
                ids.append(row['id'])
        return sorted(ids)

    def retry_failed_job(self, job_id, reason):
        if reason not in ('operator_retry', 'configuration_fixed', 'upstream_recovered'):
            raise ValueError('job_retry_reason_invalid')
        with atomic(self):
            self.recover_jobs()
            row = self.scheduled_job(job_id)
            current = db_now(self.conn)
            if not row or row['status'] != 'failed' or row['generation'] != self.job_generation(row['logical_key']):
                raise ValueError('job_retry_not_allowed')
            if row['expires_at'] is not None and row['expires_at'] <= current:
                raise ValueError('job_window_expired')
            if row['kind'] == 'auto_plan':
                source = self.scheduled_job(row['payload'].get('source_job_id'))
                if not source or source['status'] != 'done' or source['generation'] != self.job_generation(source['logical_key']):
                    raise ValueError('job_source_deleted')
            self.conn.execute('INSERT INTO job_admin_actions VALUES (?,?,?,?,?)',
                              (str(uuid4()), job_id, 'retry', reason, current))
            self.conn.execute("UPDATE scheduled_jobs SET status='pending',max_attempts=attempts+1,next_attempt_at=?,finished_at=NULL WHERE id=?",
                              (current, job_id))

    def job_status(self):
        with self.lock:
            counts = {r[0]: r[1] for r in self.conn.execute('SELECT status,count(*) FROM scheduled_jobs GROUP BY status')}
            oldest = self.conn.execute("SELECT MIN(next_attempt_at) FROM scheduled_jobs WHERE status='pending'").fetchone()[0]
            return {'counts': counts, 'oldest_due_delay_ms': max(0, db_now(self.conn)-oldest) if oldest else 0,
                    'outbox_pending': self.conn.execute("SELECT count(*) FROM outbox_events WHERE status='pending'").fetchone()[0],
                    'outbox_failed': self.conn.execute("SELECT count(*) FROM outbox_events WHERE status='failed'").fetchone()[0],
                    'lease_recoveries': self.conn.execute("SELECT count(*) FROM job_attempts WHERE error_category='runtime_timeout'").fetchone()[0],
                    'last_planned_at': self.get_meta('scheduler_last_planned_at'),
                    'paused': self.get_meta('jobs_paused', False),
                    'failed_jobs': [dict(row) for row in self.conn.execute(
                        "SELECT id,kind,attempts,error_category,error_code FROM scheduled_jobs WHERE status='failed' ORDER BY finished_at DESC LIMIT 20")]}

    def prune_job_history(self, days=30):
        with atomic(self):
            cutoff = db_now(self.conn)-days*86400000
            # Keep active references; preserve minimal task/event identities for dedupe.
            self.conn.execute('''DELETE FROM job_attempts WHERE finished_at<? AND job_id IN (
                SELECT j.id FROM scheduled_jobs j WHERE j.status IN ('done','failed','skipped','cancelled')
                AND NOT EXISTS(SELECT 1 FROM workflows w WHERE w.id=j.result_ref))''', (cutoff,))
            self.conn.execute('''DELETE FROM outbox_attempts WHERE finished_at<? AND event_id IN (
                SELECT id FROM outbox_events WHERE status IN ('consumed','failed','cancelled'))''', (cutoff,))
            self.conn.execute("UPDATE outbox_events SET payload='{}' WHERE created_at<? AND status IN ('consumed','failed','cancelled')", (cutoff,))
            self.conn.execute('''UPDATE scheduled_jobs SET payload='{}',result_ref=NULL
                WHERE finished_at<? AND status IN ('done','skipped','cancelled')
                AND NOT EXISTS(SELECT 1 FROM workflows w WHERE w.id=scheduled_jobs.result_ref)
                AND NOT EXISTS(SELECT 1 FROM outbox_events e WHERE e.source_job_id=scheduled_jobs.id AND e.status IN ('pending','running'))''', (cutoff,))
