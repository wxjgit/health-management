"""Facts, plans, insights, reviews and notification persistence."""

import json
from uuid import uuid4

from ...common.clock import stamp
from .common import dump
from ...health.derived import derived_metadata, derived_view, invalidate_data


class PlanningRepositoryMixin:
    def plans(self):
        with self.lock:
            return [
                {**dict(row), 'data': derived_view(json.loads(row['data']))}
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
        data = derived_metadata('plan', data)
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
                if plan['data'].get('fact_policy_version'):
                    from ...health.facts import FACT_POLICY_VERSION
                    from ...health.fact_resolution import require_resolved
                    from ...health.safety import require_plan_eligibility
                    require_plan_eligibility(self, plan['data'].get('targets'))
                    if plan['data']['fact_policy_version'] != FACT_POLICY_VERSION:
                        raise ValueError('事实处理规则已更新，请重新生成计划')
                    for item in plan['data'].get('schedule', []):
                        view = self.resolve_facts(item['date'], 'plan')
                        require_resolved(view)
                        if any(r['enforcement'] == 'clinical_review' for r in view['constraints']):
                            raise ValueError('计划日期存在需专业评估的信息，请重新核对')
                active = self.active_plan()
                actual_base = active['id'] if active else None
                node = self.provenance_node('plan', plan_id)
                valid = (plan['data']['dependency_stamp'] == self.read_stamp(self.node_reads(node))
                         if 'dependency_stamp' in plan['data'] else plan['data']['data_version'] == self.version())
                valid = valid and plan['data'].get('memory', {}).get('status', 'valid') == 'valid'
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
            {**dict(row), 'data': derived_view(json.loads(row['data']), rolling=True)}
            for row in rows
            if include_stale or (row['status'] == 'active' and
                                derived_view(json.loads(row['data']), rolling=True)['memory']['status'] not in ('stale', 'needs_review'))
        ]
        self.observe_scope('insights', include_stale=include_stale)
        for record in records:
            self.observe('insight', record['id'])
        return records

    def invalidate_insights(self, templates=('sleep_intake', 'weekday_intake')):
        with self.lock, self.conn:
            for template in templates:
                row = self.conn.execute("SELECT id,data FROM insights WHERE template=? AND status='active'", (template,)).fetchone()
                if row:
                    self.conn.execute("UPDATE insights SET status='stale',data=? WHERE id=?",
                                      (dump(invalidate_data(json.loads(row['data']), 'analysis_refreshed')), row['id']))

    def save_insight(self, template, data):
        data = derived_metadata(template, data)
        with self.lock, self.conn:
            old = self.conn.execute(
                'SELECT * FROM insights WHERE template=?', (template,)
            ).fetchone()
            if old and old['feedback'] == 'rejected':
                return None
            insight_id = old['id'] if old else str(uuid4())
            self.conn.execute(
                'INSERT INTO insights VALUES (?,?,?,?,?,?) '
                'ON CONFLICT(template) DO UPDATE SET data=excluded.data,status=excluded.status,created_at=excluded.created_at',
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
            row = next((r for r in self.insights(include_stale=True) if r['id'] == insight_id), None)
            if not row:
                raise ValueError('规律不存在')
            if feedback == 'accepted' and (row['status'] != 'active' or row['data']['memory']['status'] in ('stale', 'needs_review')):
                raise ValueError('此规律已过时，请重新生成复盘')
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
        data = derived_metadata('review', data)
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
            {**dict(row), 'data': derived_view(json.loads(row['data']))}
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
