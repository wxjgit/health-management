SCHEMA = '''
CREATE TABLE IF NOT EXISTS quality_observations (
 subject_type TEXT NOT NULL CHECK(subject_type IN ('event','workflow','plan')),
 subject_id TEXT NOT NULL,
 metric_key TEXT NOT NULL CHECK(metric_key IN ('nutrition_unknown','required_clarification','record_corrected','plan_outcome')),
 measure TEXT NOT NULL CHECK(measure IN ('none','kcal','protein_g','carbs_g','fat_g')),
 metric_version INTEGER NOT NULL CHECK(metric_version=1),
 cohort_day TEXT NOT NULL,
 numerator INTEGER NOT NULL CHECK(typeof(numerator)='integer' AND numerator>=0),
 denominator INTEGER NOT NULL CHECK(typeof(denominator)='integer' AND denominator>=numerator),
 outcome TEXT NOT NULL CHECK(outcome IN ('none','pending','active_decision','rejected','cancelled','stale','expired')),
 mode TEXT NOT NULL CHECK(mode IN ('local_only','model_allowed','demo')),
 decision_source TEXT NOT NULL CHECK(decision_source IN ('none','manual','automatic')),
 PRIMARY KEY(subject_type,subject_id,metric_key,measure,metric_version),
 CHECK((metric_key='nutrition_unknown' AND subject_type='event' AND measure!='none' AND outcome='none' AND decision_source='none')
 OR (metric_key='record_corrected' AND subject_type='event' AND measure='none' AND outcome='none' AND decision_source='none' AND denominator=1)
 OR (metric_key='required_clarification' AND subject_type='workflow' AND measure='none' AND outcome='none' AND decision_source='none' AND denominator=1)
 OR (metric_key='plan_outcome' AND subject_type='plan' AND measure='none' AND outcome!='none' AND numerator=1 AND denominator=1)));
CREATE INDEX IF NOT EXISTS quality_cohorts ON quality_observations(cohort_day);
'''


def install_triggers(connection):
    for table, kind in (('events', 'event'), ('workflows', 'workflow'), ('plans', 'plan')):
        connection.execute(f'''CREATE TRIGGER IF NOT EXISTS quality_delete_{kind}
            AFTER DELETE ON {table} BEGIN
            DELETE FROM quality_observations WHERE subject_type='{kind}' AND subject_id=OLD.id;
            END''')
