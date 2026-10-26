"""Facts, plans, insights, reviews and notification persistence."""

import json
from uuid import uuid4

from ...common.clock import stamp
from .common import dump


class PlanningRepositoryMixin:
    def facts(self):
        records = [
            dict(row)
            for row in self.conn.execute(
                "SELECT * FROM facts WHERE status='confirmed' ORDER BY created_at"
            )
        ]
        self.observe_scope('facts')
        for record in records:
            self.observe('fact', record['id'])
        return records

    def delete_fact(self, fact_id):
        raise RuntimeError('请通过 engine.delete_fact 执行可恢复的隐私清理')

    def save_fact(self, fact_id, content):
        with self.lock, self.conn:
            inserted = self.conn.execute(
                'INSERT OR IGNORE INTO facts VALUES (?,?,?,?,?,?)',
                (fact_id, content, 'user', None, 'confirmed', stamp()),
            ).rowcount
            if inserted:
                self._bump()
                self.invalidate_dependencies('fact', fact_id)
                node = self.provenance_node('fact', fact_id, '1', state='complete')
                from ...privacy.provenance import collectors
                for reads in collectors(self):
                    reads.sources.add(node)
                    if reads.target:
                        self.conn.execute('INSERT OR IGNORE INTO provenance_dependencies VALUES (?,?)', (node, reads.target))
                        for row in self.conn.execute('SELECT id FROM messages WHERE thread_id=? AND role=?',
                                                     (self.conn.execute('SELECT object_id FROM provenance_nodes WHERE node_id=?', (reads.target,)).fetchone()[0], 'user')):
                            self.link_source(node, row['id'], 'content')

    def plans(self):
        with self.lock:
            return [
                {**dict(row), 'data': json.loads(row['data'])}
                for row in self.conn.execute(
                    'SELECT * FROM plans ORDER BY created_at DESC'
                )
            ]

    def active_plan(self):
        plan = next(
            (plan for plan in self.plans() if plan['status'] == 'active'), None
        )
        self.observe_scope('active_plan')
        if plan:
            self.observe('plan', plan['id'], part='targets')
        return plan

    def plan(self, plan_id):
        plan = next((plan for plan in self.plans() if plan['id'] == plan_id), None)
        if plan:
            self.observe('plan', plan_id)
        return plan

    def save_plan(self, plan_id, op_key, data):
        with self.lock, self.conn:
            inserted = self.conn.execute(
                'INSERT OR IGNORE INTO plans VALUES (?,?,?,?,?)',
                (plan_id, op_key, '{}', 'pending', stamp()),
            ).rowcount
            if inserted:
                node = self.persist_provenance('plan', plan_id, data)
                self.persist_provenance('plan', plan_id, sources=data.get('_target_sources', []),
                                        part='targets', independent=True, use_reads=False)
                stored = {key: value for key, value in data.items() if key != '_target_sources'}
                stored.update(self.dependency_metadata(node))
                stored['dependency_stamp'] = self.read_stamp(self.node_reads(node))
                self.conn.execute('UPDATE plans SET data=? WHERE id=?', (dump(stored), plan_id))

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
                node = self.provenance_node('plan', plan_id)
                valid = (plan['data']['dependency_stamp'] == self.read_stamp(self.node_reads(node))
                         if 'dependency_stamp' in plan['data'] else plan['data']['data_version'] == self.version())
                status = (
                    'active'
                    if valid
                    and plan['data']['base_plan_id'] == actual_base
                    else 'stale'
                )
            if status == 'active':
                self.conn.execute(
                    "UPDATE plans SET status='superseded' WHERE status='active'"
                )
            self.conn.execute(
                'UPDATE plans SET status=? WHERE id=?', (status, plan_id)
            )
            return status

    def insights(self, include_stale=False):
        rows = self.conn.execute(
            'SELECT * FROM insights ORDER BY created_at DESC'
        ).fetchall()
        records = [
            {**dict(row), 'data': json.loads(row['data'])}
            for row in rows
            if include_stale or row['status'] == 'active'
        ]
        self.observe_scope('insights', include_stale=include_stale)
        for record in records:
            self.observe('insight', record['id'])
        return records

    def invalidate_insights(self):
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE insights SET status='stale' WHERE status='active'"
            )

    def save_insight(self, template, data):
        with self.lock, self.conn:
            old = self.conn.execute(
                'SELECT * FROM insights WHERE template=?', (template,)
            ).fetchone()
            if old and old['feedback'] == 'rejected':
                return None
            insight_id = old['id'] if old else str(uuid4())
            self.conn.execute(
                'INSERT INTO insights VALUES (?,?,?,?,?,?) '
                'ON CONFLICT(template) DO UPDATE SET data=excluded.data,status=excluded.status',
                (
                    insight_id,
                    template,
                    dump(data),
                    'active',
                    old['feedback'] if old else None,
                    stamp(),
                ),
            )
            node = self.persist_provenance('insight', insight_id, data)
            stored = {**data, **self.dependency_metadata(node)}
            self.conn.execute('UPDATE insights SET data=? WHERE id=?', (dump(stored), insight_id))
            return insight_id

    def insight_feedback(self, insight_id, feedback):
        with self.lock, self.conn:
            self._bump()
            count = self.conn.execute(
                'UPDATE insights SET feedback=?,status=? WHERE id=?',
                (
                    feedback,
                    'rejected' if feedback == 'rejected' else 'active',
                    insight_id,
                ),
            ).rowcount
            if not count:
                raise ValueError('规律不存在')

    def review(self, review_id, data):
        with self.lock, self.conn:
            self.conn.execute(
                'INSERT OR REPLACE INTO reviews VALUES (?,?,?)',
                (review_id, dump(data), stamp()),
            )
            node = self.persist_provenance('review', review_id, data)
            self.conn.execute('UPDATE reviews SET data=? WHERE id=?',
                              (dump({**data, **self.dependency_metadata(node)}), review_id))

    def reviews(self):
        return [
            {**dict(row), 'data': json.loads(row['data'])}
            for row in self.conn.execute(
                'SELECT * FROM reviews ORDER BY created_at DESC LIMIT 30'
            )
        ]

    def notify(self, key, title, body, *, sources=None):
        with self.lock, self.conn:
            notification_id = str(uuid4())
            inserted = self.conn.execute(
                'INSERT OR IGNORE INTO notifications VALUES (?,?,?,?,?,?)',
                (notification_id, key, title, body, 0, stamp()),
            ).rowcount
            if inserted:
                self.persist_provenance('notification', notification_id, sources=sources)

    def notifications(self):
        return [
            dict(row)
            for row in self.conn.execute(
                'SELECT * FROM notifications ORDER BY created_at DESC LIMIT 100'
            )
        ]

    def read_notification(self, notification_id):
        with self.lock, self.conn:
            self.conn.execute(
                'UPDATE notifications SET read=1 WHERE id=?', (notification_id,)
            )
