"""Version 8 task ledger and conservative offline legacy import."""
import json
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

SCHEMA = '''
CREATE TABLE IF NOT EXISTS scheduled_jobs (
 id TEXT PRIMARY KEY, logical_key TEXT NOT NULL, generation INTEGER NOT NULL,
 kind TEXT NOT NULL, payload TEXT NOT NULL, payload_version INTEGER NOT NULL DEFAULT 1,
 processing_mode TEXT NOT NULL CHECK(processing_mode IN ('local_only','model_allowed')),
 status TEXT NOT NULL CHECK(status IN ('pending','running','done','failed','skipped','cancelled')),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0), max_attempts INTEGER NOT NULL CHECK(max_attempts>0),
 scheduled_at INTEGER NOT NULL, next_attempt_at INTEGER NOT NULL, expires_at INTEGER,
 lease_token TEXT, lease_until INTEGER, worker_id TEXT,
 created_at INTEGER NOT NULL, started_at INTEGER, heartbeat_at INTEGER, finished_at INTEGER,
 result_ref TEXT, outcome_code TEXT, error_category TEXT, error_code TEXT,
 UNIQUE(logical_key,generation));
CREATE INDEX IF NOT EXISTS jobs_due ON scheduled_jobs(status,next_attempt_at);
CREATE INDEX IF NOT EXISTS jobs_lease ON scheduled_jobs(status,lease_until);
CREATE TABLE IF NOT EXISTS job_attempts (
 job_id TEXT NOT NULL REFERENCES scheduled_jobs(id), attempt_no INTEGER NOT NULL,
 started_at INTEGER NOT NULL, heartbeat_at INTEGER, finished_at INTEGER,
 outcome TEXT, error_category TEXT, error_code TEXT,
 PRIMARY KEY(job_id,attempt_no));
CREATE TABLE IF NOT EXISTS outbox_events (
 id TEXT PRIMARY KEY, event_key TEXT UNIQUE NOT NULL, event_type TEXT NOT NULL,
 source_job_id TEXT NOT NULL REFERENCES scheduled_jobs(id), generation INTEGER NOT NULL,
 payload TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','running','consumed','failed','cancelled')),
 attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at INTEGER NOT NULL,
 lease_token TEXT, lease_until INTEGER, created_at INTEGER NOT NULL, consumed_at INTEGER,
 error_category TEXT, error_code TEXT);
CREATE INDEX IF NOT EXISTS outbox_due ON outbox_events(status,next_attempt_at);
CREATE TABLE IF NOT EXISTS outbox_attempts (
 event_id TEXT NOT NULL REFERENCES outbox_events(id), attempt_no INTEGER NOT NULL,
 started_at INTEGER NOT NULL, finished_at INTEGER, outcome TEXT,
 error_category TEXT, error_code TEXT, PRIMARY KEY(event_id,attempt_no));
CREATE TABLE IF NOT EXISTS job_admin_actions (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES scheduled_jobs(id),
 action TEXT NOT NULL, reason TEXT NOT NULL, created_at INTEGER NOT NULL);
'''


def migrate_legacy(conn):
    current = datetime.now(timezone.utc)
    stamp = int(current.timestamp() * 1000)
    for row in conn.execute('SELECT * FROM jobs').fetchall():
        generation = conn.execute('SELECT generation FROM job_generations WHERE key=?', (row['key'],)).fetchone()
        generation = generation[0] if generation else 0
        part = row['key'].partition(':')[2]
        try:
            target = date.fromisoformat(part)
        except ValueError:
            target = None
        payload = {'period_end': part} if target else {}
        expires = None
        if target:
            days = 1 if row['kind'] == 'archive' else 7 if row['kind'] == 'weekly' else 8
            expires = int(datetime.combine(target+timedelta(days=days), datetime.min.time(),
                                          timezone(timedelta(hours=8))).timestamp()*1000)
        status, code = row['status'], None
        if status != 'done':
            if row['attempts'] >= 3:
                status, code = 'failed', 'job_attempts_exhausted'
            elif target is None or expires <= stamp:
                status, code = 'skipped', 'legacy_expired'
            else:
                status, code = 'pending', 'legacy_interrupted' if status == 'running' else None
        conn.execute('''INSERT OR IGNORE INTO scheduled_jobs
            (id,logical_key,generation,kind,payload,processing_mode,status,attempts,max_attempts,
             scheduled_at,next_attempt_at,created_at,finished_at,outcome_code,error_code,expires_at)
            VALUES (?,?,?,?,?,'local_only',?,?,3,?,?,?,?,?,?,?)''',
            (str(uuid4()), row['key'], generation, row['kind'], json.dumps(payload), status,
             row['attempts'], stamp, stamp, stamp, stamp if status != 'pending' else None,
             'legacy_import', code, expires))
