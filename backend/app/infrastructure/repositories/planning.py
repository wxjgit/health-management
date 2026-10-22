"""Facts, plans, insights, reviews and notification persistence."""

import json
from uuid import uuid4

from ...common.clock import stamp
from .common import dump


class PlanningRepositoryMixin:
    def facts(self):
        return [
            dict(row)
            for row in self.conn.execute(
                "SELECT * FROM facts WHERE status='confirmed' ORDER BY created_at"
            )
        ]

    def delete_fact(self, fact_id):
        with self.lock, self.conn:
            deleted = self.conn.execute(
                'DELETE FROM facts WHERE id=?', (fact_id,)
            ).rowcount
            if deleted:
                self._bump()
            return bool(deleted)

    def save_fact(self, fact_id, content):
        with self.lock, self.conn:
            inserted = self.conn.execute(
                'INSERT OR IGNORE INTO facts VALUES (?,?,?,?,?,?)',
                (fact_id, content, 'user', None, 'confirmed', stamp()),
            ).rowcount
            if inserted:
                self._bump()

    def plans(self):
        return [
            {**dict(row), 'data': json.loads(row['data'])}
            for row in self.conn.execute(
                'SELECT * FROM plans ORDER BY created_at DESC'
            )
        ]

    def active_plan(self):
        return next(
            (plan for plan in self.plans() if plan['status'] == 'active'), None
        )

    def plan(self, plan_id):
        return next((plan for plan in self.plans() if plan['id'] == plan_id), None)

    def save_plan(self, plan_id, op_key, data):
        with self.lock, self.conn:
            self.conn.execute(
                'INSERT OR IGNORE INTO plans VALUES (?,?,?,?,?)',
                (plan_id, op_key, dump(data), 'pending', stamp()),
            )

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
                status = (
                    'active'
                    if plan['data']['data_version'] == self.version()
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
        return [
            {**dict(row), 'data': json.loads(row['data'])}
            for row in rows
            if include_stale or row['status'] == 'active'
        ]

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

    def reviews(self):
        return [
            {**dict(row), 'data': json.loads(row['data'])}
            for row in self.conn.execute(
                'SELECT * FROM reviews ORDER BY created_at DESC LIMIT 30'
            )
        ]

    def notify(self, key, title, body):
        with self.lock, self.conn:
            self.conn.execute(
                'INSERT OR IGNORE INTO notifications VALUES (?,?,?,?,?,?)',
                (str(uuid4()), key, title, body, 0, stamp()),
            )

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
