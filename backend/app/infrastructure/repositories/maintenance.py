"""Jobs, retention, privacy cleanup and aggregate export persistence."""

import json

from ...common.clock import stamp
from .common import dump
from ...health.derived import derived_metadata, derived_view


class MaintenanceRepositoryMixin:
    def compact(self):
        with self.lock:
            if self.conn.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]:
                raise RuntimeError('数据库正忙，清理尚未完成')
            self.conn.execute('VACUUM')
            if self.conn.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]:
                raise RuntimeError('数据库正忙，清理尚未完成')

    def summaries(self):
        return [
            {**dict(row), 'data': derived_view(json.loads(row['data']))}
            for row in self.conn.execute(
                'SELECT * FROM monthly_summaries ORDER BY month DESC'
            )
        ]

    def archive_months(self, batches):
        """Atomically replace expired event details with verified monthly summaries."""
        archived = []
        with self.lock, self.conn:
            for month, events, data in batches:
                data = derived_metadata('summary', data)
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
                    self.conn.execute("UPDATE provenance_nodes SET lifecycle='archived' WHERE object_type='event' AND object_id=?", (event['id'],))
                self.persist_provenance('summary', month, sources=[
                    self.provenance_node('event', event['id'], event['version']) for event in events
                ], use_reads=False)
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
            self.conn.execute('DELETE FROM quality_observations')
            for table in [
                'messages',
                'workflows',
                'receipts',
                'resume_attempts',
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
        facts = self.facts(include_inactive=True)
        events = self.events()
        return {
            'profile': self.get_meta('profile'),
            'preferences': self.get_meta('preferences'),
            'events': events,
            'event_revisions': {event['id']: self.event_revisions(event['id']) for event in events},
            'facts': facts,
            'fact_revisions': {fact['id']: self.fact_revisions(fact['id']) for fact in facts},
            'plans': self.plans(),
            'insights': self.insights(),
            'monthly_summaries': self.summaries(),
            'reviews': self.reviews(),
        }
