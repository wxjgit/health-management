"""Jobs, retention, privacy cleanup and aggregate export persistence."""

import json

from ...common.clock import stamp
from .common import dump


class MaintenanceRepositoryMixin:
    def compact(self):
        with self.lock:
            self.conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            self.conn.execute('VACUUM')

    def start_job(self, key, kind):
        with self.lock, self.conn:
            self.conn.execute(
                'INSERT OR IGNORE INTO jobs VALUES (?,?,?,?,?)',
                (key, kind, 'pending', 0, stamp()),
            )
            row = self.conn.execute(
                'SELECT * FROM jobs WHERE key=?', (key,)
            ).fetchone()
            if row['status'] == 'done' or row['attempts'] >= 3:
                return False
            self.conn.execute(
                "UPDATE jobs SET status='running',attempts=attempts+1 WHERE key=?",
                (key,),
            )
            return True

    def finish_job(self, key, ok=True):
        with self.lock, self.conn:
            self.conn.execute(
                'UPDATE jobs SET status=? WHERE key=?',
                ('done' if ok else 'pending', key),
            )

    def summaries(self):
        return [
            {**dict(row), 'data': json.loads(row['data'])}
            for row in self.conn.execute(
                'SELECT * FROM monthly_summaries ORDER BY month DESC'
            )
        ]

    def archive_months(self, batches):
        """Atomically replace expired event details with verified monthly summaries."""
        archived = []
        with self.lock, self.conn:
            for month, events, data in batches:
                self.conn.execute(
                    'INSERT OR REPLACE INTO monthly_summaries VALUES (?,?,?)',
                    (month, dump(data), stamp()),
                )
                check = self.conn.execute(
                    'SELECT data FROM monthly_summaries WHERE month=?', (month,)
                ).fetchone()
                if json.loads(check['data'])['record_count'] != len(events):
                    raise ValueError('月度摘要校验失败，保留原记录')
                for event in events:
                    self.conn.execute(
                        'DELETE FROM facts WHERE source_event=?', (event['id'],)
                    )
                    self.conn.execute(
                        'DELETE FROM events WHERE id=?', (event['id'],)
                    )
                completion_keys = [
                    row['key']
                    for row in self.conn.execute(
                        "SELECT key FROM meta WHERE key LIKE 'complete:%'"
                    )
                    if row['key'][9:].startswith(month)
                ]
                for key in completion_keys:
                    self.conn.execute('DELETE FROM meta WHERE key=?', (key,))
                archived.append(month)
            if archived:
                self._bump()
        return archived

    def erase_context(self):
        # Queries/checkpoints can contain copies of the deleted event. Clear all transient context.
        with self.lock, self.conn:
            for table in [
                'messages',
                'workflows',
                'receipts',
                'operations',
                'reviews',
                'notifications',
                'insights',
                'model_calls',
            ]:
                self.conn.execute(f'DELETE FROM {table}')
            self.conn.execute(
                "UPDATE plans SET data='{}',status='purged' WHERE status!='active'"
            )
            for plan in self.plans():
                if plan['status'] == 'active':
                    data = plan['data']
                    data['evidence_refs'] = []
                    data['schedule'] = []
                    data['next_meal'] = '相关记录已清理，请刷新计划安排。'
                    data['tomorrow_focus'] = '相关记录已清理，请刷新计划安排。'
                    data['notes'] = '相关记录清理后，请重新生成具体安排。数值目标保留。'
                    self.conn.execute(
                        'UPDATE plans SET data=? WHERE id=?',
                        (dump(data), plan['id']),
                    )

    def export(self):
        return {
            'profile': self.get_meta('profile'),
            'preferences': self.get_meta('preferences'),
            'events': self.events(),
            'facts': self.facts(),
            'plans': self.plans(),
            'insights': self.insights(),
            'monthly_summaries': self.summaries(),
            'reviews': self.reviews(),
        }
