"""Transactional metadata contributions; never exports subject identifiers."""
from datetime import timedelta

from ...common.clock import now
from ...observability.context import processing_mode


def cohort_today():
    return now().date()


class ObservabilityRepositoryMixin:
    def quality_prune(self):
        self.conn.execute('DELETE FROM quality_observations WHERE cohort_day<?',
                          ((cohort_today()-timedelta(days=29)).isoformat(),))

    def quality_insert(self, subject_type, subject_id, metric_key, *, measure='none',
                       numerator=0, denominator=1, outcome='none', mode=None):
        self.quality_prune()
        self.conn.execute('''INSERT INTO quality_observations
            VALUES (?,?,?,?,1,?,?,?,?,?,?)
            ON CONFLICT(subject_type,subject_id,metric_key,measure,metric_version) DO NOTHING''', (subject_type, subject_id, metric_key, measure,
            cohort_today().isoformat(), numerator, denominator, outcome,
            mode or processing_mode.get() or getattr(self, 'quality_default_mode', 'demo'), 'none'))

    def quality_record(self, event_id, draft, *, correction=False, user_correction=True):
        if correction:
            if user_correction:
                self.conn.execute("UPDATE quality_observations SET numerator=1 WHERE subject_type='event' "
                    "AND subject_id=? AND metric_key='record_corrected' AND cohort_day>=?",
                    (event_id, (cohort_today()-timedelta(days=29)).isoformat()))
            return
        self.quality_insert('event', event_id, 'record_corrected')
        if draft['kind'] == 'meal':
            items = draft.get('items', [])
            for nutrient in ('kcal', 'protein_g', 'carbs_g', 'fat_g'):
                self.quality_insert('event', event_id, 'nutrition_unknown', measure=nutrient,
                    numerator=sum(item.get(nutrient) is None for item in items), denominator=len(items))

    def quality_clarification(self, thread_id, result):
        pending = result.get('pending_input') or {}
        if (result.get('status') == 'waiting_input' and pending.get('kind') == 'clarification'
                and pending.get('optional') is False):
            self.conn.execute("UPDATE quality_observations SET numerator=1 WHERE subject_type='workflow' "
                "AND subject_id=? AND metric_key='required_clarification'", (thread_id,))

    def quality_plan_outcome(self, plan_id, outcome, source='none'):
        self.conn.execute("UPDATE quality_observations SET outcome=?,decision_source=? "
            "WHERE subject_type='plan' AND subject_id=? AND metric_key='plan_outcome' AND outcome='pending'",
            (outcome, source, plan_id))

    def quality_snapshot(self):
        # Fresh query under caller's read fence: never keeps a deleted contribution cached.
        return [dict(row) for row in self.conn.execute('''SELECT metric_key,measure,outcome,decision_source,
            sum(numerator) AS numerator,sum(denominator) AS denominator FROM quality_observations
            WHERE cohort_day>=? GROUP BY metric_key,measure,outcome,decision_source ORDER BY metric_key,measure,outcome,decision_source''',
            ((cohort_today()-timedelta(days=29)).isoformat(),))]
