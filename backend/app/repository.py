"""SQLite is the source of truth; LangGraph checkpoints only track execution."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4

from .clock import stamp, local_day
from .schemas import Preferences


def dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class Repository:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute('PRAGMA foreign_keys=ON')
        self.conn.executescript('''
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, op_key TEXT UNIQUE NOT NULL, kind TEXT NOT NULL,
                occurred_at TEXT NOT NULL, day TEXT NOT NULL, raw_text TEXT NOT NULL,
                data TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS events_day ON events(day, kind);
            CREATE TABLE IF NOT EXISTS revisions (
                event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                version INTEGER NOT NULL, data TEXT NOT NULL, raw_text TEXT NOT NULL,
                reason TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(event_id, version));
            CREATE TABLE IF NOT EXISTS operations (key TEXT PRIMARY KEY, result TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS workflows (
                id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
                kind TEXT NOT NULL, status TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS receipts (
                request_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                result TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
                role TEXT NOT NULL, text TEXT NOT NULL, cards TEXT NOT NULL,
                evidence_refs TEXT NOT NULL, created_at TEXT NOT NULL,
                process_trace TEXT NOT NULL DEFAULT '[]', duration_ms INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS model_calls (
                thread_id TEXT NOT NULL, purpose TEXT NOT NULL, payload_bytes INTEGER NOT NULL,
                created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS facts (
                id TEXT PRIMARY KEY, content TEXT NOT NULL, source TEXT NOT NULL,
                source_event TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS plans (
                id TEXT PRIMARY KEY, op_key TEXT UNIQUE NOT NULL, data TEXT NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS insights (
                id TEXT PRIMARY KEY, template TEXT UNIQUE NOT NULL, data TEXT NOT NULL,
                status TEXT NOT NULL, feedback TEXT, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS reviews (
                id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY, dedupe_key TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
                body TEXT NOT NULL, read INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs (
                key TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS monthly_summaries (
                month TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL);
            PRAGMA user_version=1;
        ''')
        message_columns = {row['name'] for row in self.conn.execute('PRAGMA table_info(messages)').fetchall()}
        if 'process_trace' not in message_columns:
            self.conn.execute("ALTER TABLE messages ADD COLUMN process_trace TEXT NOT NULL DEFAULT '[]'")
        if 'duration_ms' not in message_columns:
            self.conn.execute('ALTER TABLE messages ADD COLUMN duration_ms INTEGER NOT NULL DEFAULT 0')
        self.conn.execute('PRAGMA user_version=3')
        self.conn.commit()
        if self.get_meta('preferences') is None:
            self.set_meta('preferences', Preferences().model_dump())
        if self.get_meta('data_version') is None:
            self.set_meta('data_version', 0)

    def close(self):
        self.conn.close()

    def account_model_call(self, thread_id, purpose, payload_bytes):
        """Reserve the workflow budget before dispatch, also for failed/retried calls.

        Only metadata is kept; neither request bodies nor health text are logged.
        """
        with self.lock, self.conn:
            count, size = self.conn.execute(
                'SELECT COUNT(*), COALESCE(SUM(payload_bytes),0) FROM model_calls WHERE thread_id=?',
                (thread_id,)).fetchone()
            if count >= 32 or size + payload_bytes > 400000:
                raise ValueError('本次流程已达到模型调用预算，请缩小任务或重新开始。')
            if purpose == 'processing_plan':
                plans = self.conn.execute(
                    "SELECT COUNT(*) FROM model_calls WHERE thread_id=? AND purpose='processing_plan'",
                    (thread_id,)).fetchone()[0]
                if plans >= 4:
                    raise ValueError('本次流程已达到重新规划上限，请明确任务后重新开始。')
            self.conn.execute('INSERT INTO model_calls VALUES (?,?,?,?)',
                              (thread_id, purpose, payload_bytes, stamp()))

    def get_meta(self, key, default=None):
        with self.lock:
            row = self.conn.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
            return json.loads(row['value']) if row else default

    def set_meta(self, key, value):
        with self.lock, self.conn:
            self.conn.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, dump(value)))

    def version(self):
        return self.get_meta('data_version', 0)

    def _bump(self):
        value = self.version() + 1
        self.conn.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('data_version', dump(value)))
        self.conn.execute("UPDATE insights SET status='stale' WHERE status='active'")

    def update_profile(self, value):
        with self.lock, self.conn:
            self.conn.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('profile', dump(value)))
            self._bump()

    @staticmethod
    def event_row(row):
        if row is None:
            return None
        return {**dict(row), 'data': json.loads(row['data'])}

    def events(self, start=None, end=None, kind=None, search=None, limit=None):
        where, args = [], []
        for column, op, value in [('day', '>=', start), ('day', '<=', end), ('kind', '=', kind)]:
            if value:
                where.append(f'{column}{op}?')
                args.append(value)
        if search:
            where.append('raw_text LIKE ?')
            args.append(f'%{search}%')
        query = 'SELECT * FROM events' + (' WHERE ' + ' AND '.join(where) if where else '')
        query += ' ORDER BY occurred_at DESC, created_at DESC'
        if limit:
            query += ' LIMIT ?'
            args.append(limit)
        with self.lock:
            return [self.event_row(r) for r in self.conn.execute(query, args).fetchall()]

    def event(self, event_id):
        with self.lock:
            return self.event_row(self.conn.execute('SELECT * FROM events WHERE id=?', (event_id,)).fetchone())

    def save_events(self, drafts, op_key, correction_id=None):
        with self.lock, self.conn:
            old = self.conn.execute('SELECT result FROM operations WHERE key=?', (op_key,)).fetchone()
            if old:
                return json.loads(old['result'])
            saved = []
            if correction_id and len(drafts) != 1:
                raise ValueError('一次纠错只能对应一条记录')
            for index, draft in enumerate(drafts):
                event_id = correction_id or str(uuid4())
                prior = self.event(event_id) if correction_id else None
                if correction_id and prior is None:
                    raise ValueError('要修正的记录已不存在')
                revision = prior['version'] + 1 if prior else 1
                occurred = datetime.fromisoformat(draft['occurred_at']).astimezone(timezone.utc).isoformat()
                draft = {**draft, 'occurred_at': occurred}
                data = dump(draft)
                if prior:
                    self.conn.execute('UPDATE events SET kind=?, occurred_at=?, day=?, raw_text=?, data=?, version=? WHERE id=?',
                                      (draft['kind'], occurred, local_day(occurred), draft['raw_text'], data, revision, event_id))
                else:
                    self.conn.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?)',
                                      (event_id, f'{op_key}:{index}', draft['kind'], occurred, local_day(occurred), draft['raw_text'], data, revision, stamp()))
                self.conn.execute('INSERT INTO revisions VALUES (?,?,?,?,?,?)',
                                  (event_id, revision, data, draft['raw_text'], '纠正记录' if prior else '首次记录', stamp()))
                saved.append(event_id)
            self.conn.execute('INSERT INTO operations VALUES (?,?)', (op_key, dump(saved)))
            if saved:
                self._bump()
            return saved

    def mark_complete(self, day, complete):
        with self.lock, self.conn:
            self.conn.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (f'complete:{day}', dump(complete)))
            self._bump()

    def workflow(self, thread_id):
        row = self.conn.execute('SELECT * FROM workflows WHERE id=?', (thread_id,)).fetchone()
        return {**dict(row), 'result': json.loads(row['result'])} if row else None

    def workflow_by_request(self, request_id):
        row = self.conn.execute('SELECT id FROM workflows WHERE request_id=?', (request_id,)).fetchone()
        return self.workflow(row['id']) if row else None

    def register_workflow(self, thread_id, request_id, fingerprint, kind):
        with self.lock, self.conn:
            self.conn.execute('INSERT INTO workflows VALUES (?,?,?,?,?,?,?)',
                              (thread_id, request_id, fingerprint, kind, 'running', '{}', stamp()))

    def workflow_result(self, thread_id, result):
        with self.lock, self.conn:
            self.conn.execute('UPDATE workflows SET status=?, result=? WHERE id=?', (result['status'], dump(result), thread_id))

    def messages(self, conversation_id='main', limit=100):
        rows = self.conn.execute('SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at DESC LIMIT ?', (conversation_id, limit)).fetchall()
        return [{**dict(r), 'cards': json.loads(r['cards']), 'evidence_refs': json.loads(r['evidence_refs']),
                 'process_trace': json.loads(r['process_trace'])} for r in reversed(rows)]

    def previous_reference_ids(self, conversation_id, excluding_thread):
        # Reference resolution reads only local result metadata, not chat text.
        row = self.conn.execute(
            "SELECT evidence_refs FROM messages WHERE conversation_id=? AND role='assistant' "
            'AND thread_id!=? ORDER BY created_at DESC, rowid DESC LIMIT 1',
            (conversation_id, excluding_thread)).fetchone()
        return list(dict.fromkeys(ref['id'] for ref in json.loads(row[0])))[:5] if row else []

    def message(self, message_id, thread_id, role, text, cards=None, evidence=None, conversation='main',
                process_trace=None, duration_ms=0):
        with self.lock, self.conn:
            self.conn.execute('''INSERT OR IGNORE INTO messages
                (id,thread_id,conversation_id,role,text,cards,evidence_refs,created_at,process_trace,duration_ms)
                VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (message_id, thread_id, conversation, role, text, dump(cards or []), dump(evidence or []),
                 stamp(), dump(process_trace or []), max(0, int(duration_ms))))

    def receipt(self, request_id):
        row = self.conn.execute('SELECT * FROM receipts WHERE request_id=?', (request_id,)).fetchone()
        return {**dict(row), 'result': json.loads(row['result'])} if row else None

    def save_receipt(self, request_id, thread_id, fingerprint, result):
        with self.lock, self.conn:
            self.conn.execute('INSERT OR REPLACE INTO receipts VALUES (?,?,?,?)', (request_id, thread_id, fingerprint, dump(result)))

    def facts(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM facts WHERE status='confirmed' ORDER BY created_at")]

    def save_fact(self, fact_id, content):
        with self.lock, self.conn:
            inserted = self.conn.execute('INSERT OR IGNORE INTO facts VALUES (?,?,?,?,?,?)',
                                         (fact_id, content, 'user', None, 'confirmed', stamp())).rowcount
            if inserted:
                self._bump()

    def plans(self):
        return [{**dict(r), 'data': json.loads(r['data'])} for r in self.conn.execute('SELECT * FROM plans ORDER BY created_at DESC')]

    def active_plan(self):
        return next((p for p in self.plans() if p['status'] == 'active'), None)

    def plan(self, plan_id):
        return next((p for p in self.plans() if p['id'] == plan_id), None)

    def save_plan(self, plan_id, op_key, data):
        with self.lock, self.conn:
            self.conn.execute('INSERT OR IGNORE INTO plans VALUES (?,?,?,?,?)', (plan_id, op_key, dump(data), 'pending', stamp()))

    def decide_plan(self, plan_id, approved):
        with self.lock, self.conn:
            plan = self.plan(plan_id)
            if not plan:
                raise ValueError('计划不存在')
            if plan['status'] != 'pending':
                return plan['status']
            if not approved:
                status = 'rejected'
            else:
                active = self.active_plan()
                actual_base = active['id'] if active else None
                status = 'active' if plan['data']['data_version'] == self.version() and plan['data']['base_plan_id'] == actual_base else 'stale'
            if status == 'active':
                self.conn.execute("UPDATE plans SET status='superseded' WHERE status='active'")
            self.conn.execute('UPDATE plans SET status=? WHERE id=?', (status, plan_id))
            return status

    def insights(self, include_stale=False):
        rows = self.conn.execute('SELECT * FROM insights ORDER BY created_at DESC').fetchall()
        return [{**dict(r), 'data': json.loads(r['data'])} for r in rows if include_stale or r['status'] == 'active']

    def save_insight(self, template, data):
        with self.lock, self.conn:
            old = self.conn.execute('SELECT * FROM insights WHERE template=?', (template,)).fetchone()
            if old and old['feedback'] == 'rejected':
                return None
            insight_id = old['id'] if old else str(uuid4())
            self.conn.execute('INSERT INTO insights VALUES (?,?,?,?,?,?) ON CONFLICT(template) DO UPDATE SET data=excluded.data,status=excluded.status',
                              (insight_id, template, dump(data), 'active', old['feedback'] if old else None, stamp()))
            return insight_id

    def insight_feedback(self, insight_id, feedback):
        with self.lock, self.conn:
            self._bump()
            count = self.conn.execute('UPDATE insights SET feedback=?,status=? WHERE id=?',
                                      (feedback, 'rejected' if feedback == 'rejected' else 'active', insight_id)).rowcount
            if not count:
                raise ValueError('规律不存在')

    def review(self, review_id, data):
        with self.lock, self.conn:
            self.conn.execute('INSERT OR REPLACE INTO reviews VALUES (?,?,?)', (review_id, dump(data), stamp()))

    def reviews(self):
        return [{**dict(r), 'data': json.loads(r['data'])} for r in self.conn.execute('SELECT * FROM reviews ORDER BY created_at DESC LIMIT 30')]

    def notify(self, key, title, body):
        with self.lock, self.conn:
            self.conn.execute('INSERT OR IGNORE INTO notifications VALUES (?,?,?,?,?,?)', (str(uuid4()), key, title, body, 0, stamp()))

    def notifications(self):
        return [dict(r) for r in self.conn.execute('SELECT * FROM notifications ORDER BY created_at DESC LIMIT 100')]

    def read_notification(self, notification_id):
        with self.lock, self.conn:
            self.conn.execute('UPDATE notifications SET read=1 WHERE id=?', (notification_id,))

    def start_job(self, key, kind):
        with self.lock, self.conn:
            self.conn.execute('INSERT OR IGNORE INTO jobs VALUES (?,?,?,?,?)', (key, kind, 'pending', 0, stamp()))
            row = self.conn.execute('SELECT * FROM jobs WHERE key=?', (key,)).fetchone()
            if row['status'] == 'done' or row['attempts'] >= 3:
                return False
            self.conn.execute("UPDATE jobs SET status='running',attempts=attempts+1 WHERE key=?", (key,))
            return True

    def finish_job(self, key, ok=True):
        with self.lock, self.conn:
            self.conn.execute('UPDATE jobs SET status=? WHERE key=?', ('done' if ok else 'pending', key))

    def summaries(self):
        return [{**dict(r), 'data': json.loads(r['data'])} for r in self.conn.execute('SELECT * FROM monthly_summaries ORDER BY month DESC')]

    def erase_context(self):
        # Queries/checkpoints can contain copies of the deleted event. Clear all transient context.
        with self.lock, self.conn:
            for table in ['messages', 'workflows', 'receipts', 'operations', 'reviews', 'notifications', 'insights', 'model_calls']:
                self.conn.execute(f'DELETE FROM {table}')
            self.conn.execute("UPDATE plans SET data='{}',status='purged' WHERE status!='active'")
            for plan in self.plans():
                if plan['status'] == 'active':
                    data = plan['data']
                    data['evidence_refs'] = []
                    data['schedule'] = []
                    data['next_meal'] = '相关记录已清理，请刷新计划安排。'
                    data['tomorrow_focus'] = '相关记录已清理，请刷新计划安排。'
                    data['notes'] = '相关记录清理后，请重新生成具体安排。数值目标保留。'
                    self.conn.execute('UPDATE plans SET data=? WHERE id=?', (dump(data), plan['id']))

    def delete_event(self, event_id):
        with self.lock, self.conn:
            if not self.event(event_id):
                raise ValueError('记录不存在')
            self.conn.execute('DELETE FROM events WHERE id=?', (event_id,))
            self.conn.execute('DELETE FROM facts WHERE source_event=?', (event_id,))
            self._bump()
        self.erase_context()

    def export(self):
        return {'profile': self.get_meta('profile'), 'preferences': self.get_meta('preferences'),
                'events': self.events(), 'facts': self.facts(), 'plans': self.plans(),
                'insights': self.insights(), 'monthly_summaries': self.summaries(), 'reviews': self.reviews()}
