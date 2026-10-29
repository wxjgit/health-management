"""Memory lifecycle tests using isolated databases; no external model calls."""

from app.common.errors import ConflictError
from datetime import timedelta
from uuid import uuid4
from threading import Event, Thread

import pytest
from authenticated_client import AuthenticatedClient as TestClient

from app.common import clock
from app.common.config import Settings
from app.contracts.facts import FactInput
from app.health.analysis import review
from app.health.derived import ALGORITHMS
from app.health.planning import plan_draft
from app.health.safety import local_safety
from app.privacy.provenance import capture
from app.workflows.engine import Engine
from app.workflows.concurrency import WorkflowConflict
from app.main import create_app

TARGETS = {'kcal': 1800, 'protein_g': 100, 'carbs_g': 215, 'fat_g': 60, 'exercise_minutes': 120}


@pytest.fixture
def engine(tmp_path):
    runtime = Engine(Settings(data_dir=tmp_path, scheduler_enabled=False), user_id='00000000-0000-4000-8000-000000000001')
    yield runtime
    runtime.close()


def record(runtime, value=65, key=None, event_id=None, expected_version=None):
    return runtime.repo.save_events([{'kind': 'weight', 'value': value, 'raw_text': f'体重{value}公斤',
                                     'occurred_at': clock.now().isoformat()}], key or str(uuid4()),
                                    event_id, expected_version=expected_version)[0]


def chat(runtime, text, request_id=None):
    request_id = request_id or str(uuid4())
    return runtime.run('chat', request_id, {'text': text}, {'input_text': text, 'processing_mode': 'local_only'})


def test_explicit_preference_replaces_old_and_replays_without_revision(engine):
    old = engine.repo.save_fact('old', '我不喜欢燕麦')
    new = engine.repo.save_fact('new', '我现在可以接受燕麦')
    assert old['type'] == 'food_preference'
    assert new['supersedes'] == old['id']
    assert [f['id'] for f in engine.repo.facts()] == ['new']
    assert engine.repo.fact('old')['lifecycle'] == 'superseded'
    assert len(engine.repo.fact_revisions('old')) == 2
    engine.repo.save_fact('new', '我现在可以接受燕麦')
    assert len(engine.repo.fact_revisions('new')) == 1


def test_duplicate_preference_is_not_saved_twice(engine):
    engine.repo.save_fact('first', '我不喜欢燕麦')
    again = engine.repo.save_fact('second', '我不喜欢燕麦')
    assert again['id'] == 'first'
    assert len(engine.repo.facts()) == 1


def test_temporary_preference_does_not_erase_permanent_one(engine):
    engine.repo.save_fact('permanent', '我不喜欢燕麦')
    temporary = engine.repo.save_fact('temporary', '我这周可以接受燕麦')
    assert temporary['confirmation'] == 'conflict'
    assert engine.repo.fact('permanent')['lifecycle'] == 'active'
    assert engine.repo.facts() == []
    chosen = engine.repo.decide_fact('temporary', 'confirm', temporary['version'])
    assert chosen['confirmation'] == 'confirmed'
    assert engine.repo.fact('permanent')['lifecycle'] == 'active'
    assert chosen['overrides'] == ['permanent']
    assert [f['id'] for f in engine.repo.facts()] == ['temporary']


def test_fact_edit_uses_expected_version_and_retains_snapshots(engine):
    first = engine.repo.save_fact('fact', '我不喜欢燕麦')
    changed = engine.repo.save_fact('fact', '我喜欢燕麦', expected_version=first['version'])
    with pytest.raises(ConflictError):
        engine.repo.save_fact('fact', '我不吃燕麦', expected_version=first['version'])
    assert changed['version'] == 2
    assert [r['content'] for r in engine.repo.fact_revisions('fact')] == ['我不喜欢燕麦', '我喜欢燕麦']
    engine.repo.decide_fact('fact', 'retract', changed['version'])
    assert engine.repo.facts() == []


def test_expired_preference_stops_and_expired_restriction_stays_protective(engine):
    yesterday = clock.now().date() - timedelta(days=1)
    engine.repo.save_fact('temporary', '临时不吃辣', structured=FactInput(
        content='临时不吃辣', type='food_preference', subject='辣', value='avoid',
        restriction_reason='preference', valid_until=yesterday))
    engine.repo.save_fact('allergy', '花生', structured=FactInput(
        content='花生', type='allergy', subject='花生', value='allergic', valid_until=yesterday))
    engine.repo.expire_facts()
    assert engine.repo.fact('temporary')['lifecycle'] == 'expired'
    assert engine.repo.fact('allergy')['lifecycle'] == 'needs_review'
    assert local_safety(engine.repo)['category'] == 'medical'


def test_legacy_fact_remains_readable_without_invented_type(engine):
    with engine.repo.conn:
        engine.repo.conn.execute('INSERT INTO facts VALUES (?,?,?,?,?,?)',
                                ('legacy', '医生要求控制饮食', 'user', None, 'confirmed', clock.stamp()))
    legacy = engine.repo.fact('legacy')
    assert legacy['type'] == 'legacy_text' and legacy['source_kind'] == 'legacy'
    assert local_safety(engine.repo)['category'] == 'medical'


def test_deletion_cascades_fact_snapshots(engine):
    fact = engine.repo.save_fact('fact', '我不喜欢燕麦')
    engine.repo.save_fact('fact', '我喜欢燕麦', expected_version=fact['version'])
    assert engine.delete_fact('fact')['deleted']
    assert engine.repo.conn.execute('SELECT COUNT(*) FROM fact_details').fetchone()[0] == 0
    assert engine.repo.conn.execute('SELECT COUNT(*) FROM fact_revisions').fetchone()[0] == 0


def test_fact_api_conflict_retraction_and_history(tmp_path):
    with TestClient(create_app(
        Settings(data_dir=tmp_path, scheduler_enabled=False))) as client:
        created = client.post('/api/facts', json={'content': '我不喜欢燕麦', 'type': 'food_preference',
                                                'subject': '燕麦', 'value': 'dislike'}).json()
        changed = client.put(f'/api/facts/{created["id"]}', json={
            'content': '我喜欢燕麦', 'type': 'food_preference', 'subject': '燕麦', 'value': 'like',
            'expected_version': created['version']})
        assert changed.status_code == 200
        assert client.get(f'/api/facts/{created["id"]}/revisions').status_code == 200
        assert client.patch(f'/api/facts/{created["id"]}', json={
            'action': 'retract', 'expected_version': changed.json()['version']}).status_code == 200
        assert client.get('/api/facts').json() == []
        assert len(client.get('/api/facts?include_inactive=true').json()) == 1


def test_source_changes_mark_review_stale_but_preserve_historical_window(engine):
    event_id = record(engine)
    report = review(engine.repo, 'week')
    stored = engine.repo.reviews()[0]['data']
    assert stored['memory']['algorithm_version'] == 'review-v1'
    assert stored['source_version'][0]['id'] == event_id
    record(engine, 64, event_id=event_id, expected_version=1)
    stale = engine.repo.reviews()[0]['data']
    assert stale['memory']['invalid_reason'] == 'source_changed'
    assert stale['memory']['window'] == [report['start'], report['end']]


def test_algorithm_upgrade_blocks_old_plan_without_overwriting_targets(engine, monkeypatch):
    data = plan_draft(engine.repo, TARGETS)
    engine.repo.save_plan('draft', 'draft', data)
    monkeypatch.setitem(ALGORITHMS, 'plan', 'editable-targets-v3')
    assert engine.repo.decide_plan('draft', True) == 'stale'
    assert engine.repo.plan('draft')['data']['targets'] == TARGETS


def test_rolling_insight_expires_without_expiring_historical_review(engine, monkeypatch):
    today = clock.now().date()
    engine.repo.save_insight('weekday_intake', {'window': [str(today - timedelta(days=27)), str(today)],
                                             'rule_version': 'weekday-intake-v1'})
    review(engine.repo)
    tomorrow = clock.now() + timedelta(days=1)
    monkeypatch.setattr(clock, 'now', lambda: tomorrow)
    assert engine.repo.insights() == []
    assert engine.repo.insights(include_stale=True)[0]['data']['memory']['invalid_reason'] == 'window_changed'
    assert engine.repo.reviews()[0]['data']['memory']['status'] == 'valid'


def test_unrelated_change_does_not_stale_narrow_review(engine):
    day = clock.now().date().isoformat()
    with capture(engine.repo):
        engine.repo.events(start=day, end=day, kind='weight')
        engine.repo.review('narrow', {'start': day, 'end': day})
    engine.repo.save_events([{'kind': 'exercise', 'value': 10, 'raw_text': '跑步10分钟',
                             'occurred_at': clock.now().isoformat()}], 'unrelated')
    assert engine.repo.reviews()[0]['data']['memory']['status'] == 'valid'


def test_archived_summary_has_versions_sources_and_cannot_be_recomputed(engine):
    engine.repo.save_events([{'kind': 'weight', 'value': 65, 'raw_text': '体重65公斤',
                             'occurred_at': '2020-01-03T08:00:00+08:00'}], 'old')
    assert engine.archive() == ['2020-01']
    summary = engine.repo.summaries()[0]['data']
    assert summary['memory']['algorithm_version'] == 'monthly-summary-v1'
    assert not summary['memory']['recomputable']
    assert summary['source_event_ids']


def test_pending_expiry_keeps_saved_records_and_rejects_old_request(engine):
    request = str(uuid4())
    pending = chat(engine, '今天体重65公斤，中午吃了炸鸡', request)
    assert pending['status'] == 'waiting_input'
    assert engine.prune_context(clock.now() + timedelta(days=8)) == 1
    assert len(engine.repo.events()) == 1
    assert engine.repo.expired_contexts()[0]['saved_record_ids']
    assert list(engine.checkpointer.list(None)) == []
    with pytest.raises(WorkflowConflict, match='过期'):
        chat(engine, '今天体重65公斤，中午吃了炸鸡', request)


def test_expiry_uses_last_activity_instead_of_creation_time(engine):
    pending = chat(engine, '中午吃了炸鸡')
    with engine.repo.conn:
        engine.repo.conn.execute('UPDATE workflows SET created_at=? WHERE id=?',
                                ((clock.now() - timedelta(days=400)).isoformat(), pending['thread_id']))
    assert engine.prune_context() == 0
    assert engine.repo.workflow(pending['thread_id'])['status'] == 'waiting_input'


def test_deadline_is_enforced_even_before_scheduler_runs(engine):
    pending = chat(engine, '中午吃了炸鸡')
    with engine.repo.conn:
        engine.repo.conn.execute('UPDATE workflow_activity SET expires_at=? WHERE thread_id=?',
                                ((clock.now() - timedelta(seconds=1)).isoformat(), pending['thread_id']))
    with pytest.raises(WorkflowConflict, match='过期'):
        engine.resume(pending['thread_id'], {'request_id': str(uuid4()),
            'interrupt_id': pending['pending_input']['interrupt_id'], 'text': '一份'})


def test_expiry_failure_is_recovered_at_restart(engine, monkeypatch):
    pending = chat(engine, '中午吃了炸鸡')
    original = engine.checkpointer.delete_thread
    monkeypatch.setattr(engine.checkpointer, 'delete_thread', lambda _: (_ for _ in ()).throw(RuntimeError('disk busy')))
    with pytest.raises(RuntimeError, match='disk busy'):
        engine.prune_context(clock.now() + timedelta(days=8))
    assert engine.repo.workflow(pending['thread_id'])['status'] == 'expired'
    monkeypatch.setattr(engine.checkpointer, 'delete_thread', original)
    engine.close()
    second = Engine(engine.settings, user_id='00000000-0000-4000-8000-000000000001')
    try:
        assert second.repo.workflow(pending['thread_id']) is None
        assert list(second.checkpointer.list(None)) == []
        assert second.repo.expired_contexts()[0]['status'] == 'expired'
    finally:
        second.close()


def test_event_revisions_reject_stale_writes_and_delete_all_versions(engine):
    event_id = record(engine)
    record(engine, 64, key='correction', event_id=event_id, expected_version=1)
    with pytest.raises(ConflictError):
        record(engine, 63, event_id=event_id, expected_version=1)
    record(engine, 64, key='correction', event_id=event_id, expected_version=1)
    revisions = engine.repo.event_revisions(event_id)
    assert len(revisions) == 2
    assert revisions[1]['source_request_id'] == 'correction'
    assert any(change['field'] == 'value' and change['before'] == 65 and change['after'] == 64
               for change in revisions[1]['changes'])
    assert engine.delete_event(event_id)['deleted']
    assert engine.repo.conn.execute('SELECT COUNT(*) FROM revision_context').fetchone()[0] == 0


def test_existing_schema_upgrades_without_altering_legacy_fact(engine):
    with engine.repo.conn:
        engine.repo.conn.execute('INSERT INTO facts VALUES (?,?,?,?,?,?)',
                                ('old', '我不吃花生', 'user', None, 'confirmed', clock.stamp()))
        engine.repo.conn.execute('PRAGMA user_version=5')
    engine.close()
    second = Engine(engine.settings, user_id='00000000-0000-4000-8000-000000000001')
    try:
        assert second.repo.conn.execute('PRAGMA user_version').fetchone()[0] == 9
        assert second.repo.fact('old')['content'] == '我不吃花生'
        assert second.repo.fact('old')['type'] == 'legacy_text'
    finally:
        second.close()


def test_future_preference_does_not_replace_current_fact_before_start_date(engine, monkeypatch):
    today = clock.now()
    engine.repo.save_fact('current', '我不喜欢燕麦')
    future = engine.repo.save_fact('future', '下周接受燕麦', structured=FactInput(
        content='下周接受燕麦', type='food_preference', subject='燕麦', value='accept',
        valid_from=(today + timedelta(days=7)).date()))
    assert future['lifecycle'] == 'scheduled'
    assert [f['id'] for f in engine.repo.facts()] == ['current']
    monkeypatch.setattr(clock, 'now', lambda: today + timedelta(days=7))
    assert [f['id'] for f in engine.repo.facts()] == ['future']
    assert engine.repo.fact('current')['lifecycle'] == 'superseded'


def test_expiring_temporary_conflict_restores_original_preference(engine, monkeypatch):
    today = clock.now()
    engine.repo.save_fact('original', '我不喜欢燕麦')
    engine.repo.save_fact('today', '我今天可以接受燕麦')
    assert engine.repo.facts() == []
    monkeypatch.setattr(clock, 'now', lambda: today + timedelta(days=1))
    assert [f['id'] for f in engine.repo.facts()] == ['original']


def test_duplicate_fact_replay_does_not_replace_a_newer_preference(engine):
    engine.repo.save_fact('original', '我不喜欢燕麦')
    engine.repo.save_fact('duplicate', '我不喜欢燕麦')
    engine.repo.save_fact('newer', '我现在可以接受燕麦')
    engine.repo.save_fact('duplicate', '我不喜欢燕麦')
    assert [f['id'] for f in engine.repo.facts()] == ['newer']


def test_hypothetical_or_question_is_not_an_effective_long_term_fact(engine):
    for index, text in enumerate(['假如我不喜欢燕麦', '我喜欢燕麦吗？', '听说我应该不吃燕麦']):
        created = engine.repo.save_fact(str(index), text)
        assert created['confirmation'] == 'candidate'
    assert engine.repo.facts() == []


def test_refreshing_known_analyses_does_not_clear_unrelated_templates(engine):
    engine.repo.save_insight('custom', {'rule_version': 'custom-v1'})
    engine.repo.invalidate_insights()
    assert [row['template'] for row in engine.repo.insights()] == ['custom']


def test_expired_receipt_remains_rejected_after_restart(engine):
    pending = chat(engine, '中午吃了炸鸡')
    request = str(uuid4())
    answer = {'request_id': request, 'interrupt_id': pending['pending_input']['interrupt_id'], 'text': '一份'}
    engine.resume(pending['thread_id'], answer)
    engine.prune_context(clock.now() + timedelta(days=31))
    engine.close()
    second = Engine(engine.settings, user_id='00000000-0000-4000-8000-000000000001')
    try:
        with pytest.raises(WorkflowConflict, match='过期'):
            second.resume(pending['thread_id'], answer)
        assert len(second.repo.events()) == 1
    finally:
        second.close()


def test_preference_correction_does_not_bypass_existing_safety(engine):
    engine.repo.save_fact('allergy', '花生过敏')
    engine.repo.save_fact('preference', '我可以接受花生')
    assert local_safety(engine.repo)['category'] == 'medical'


def test_legacy_fact_snapshot_is_retained_when_it_is_superseded(engine):
    with engine.repo.conn:
        engine.repo.conn.execute('INSERT INTO facts VALUES (?,?,?,?,?,?)',
                                ('legacy', '临时限制', 'user', None, 'confirmed', clock.stamp()))
    old = engine.repo.fact('legacy')
    engine.repo.decide_fact('legacy', 'retract', old['version'])
    assert [r['version'] for r in engine.repo.fact_revisions('legacy')] == [1, 2]


def test_expiration_compares_instants_across_timestamp_offsets(engine):
    pending = chat(engine, '中午吃了炸鸡')
    with engine.repo.conn:
        engine.repo.conn.execute('UPDATE workflow_activity SET expires_at=? WHERE thread_id=?',
                                ((clock.now() - timedelta(seconds=1)).isoformat(), pending['thread_id']))
    assert engine.prune_context() == 1
    assert engine.repo.workflow(pending['thread_id']) is None


def test_completed_resume_receipt_cannot_expose_expired_context_before_maintenance(engine):
    pending = chat(engine, '中午吃了炸鸡')
    answer = {'request_id': str(uuid4()), 'interrupt_id': pending['pending_input']['interrupt_id'], 'text': '一份'}
    engine.resume(pending['thread_id'], answer)
    with engine.repo.conn:
        engine.repo.conn.execute('UPDATE workflow_activity SET expires_at=? WHERE thread_id=?',
                                ((clock.now() - timedelta(seconds=1)).isoformat(), pending['thread_id']))
    with pytest.raises(WorkflowConflict, match='过期'):
        engine.resume(pending['thread_id'], answer)


def test_message_read_expires_context_before_parallel_refresh_can_restore_it(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path, scheduler_enabled=False))) as client:
        runtime = client.app.state.test_engine
        pending = chat(runtime, '今天体重65公斤，中午吃了炸鸡')
        with runtime.repo.conn:
            runtime.repo.conn.execute('UPDATE workflow_activity SET expires_at=? WHERE thread_id=?',
                ((clock.now() - timedelta(seconds=1)).isoformat(), pending['thread_id']))
        assert client.get('/api/messages').json() == []
        assert client.get('/api/workflows').json() == []
        expired = client.get('/api/workflows/expired').json()
        assert expired[0]['saved_record_ids']
        assert len(client.get('/api/events').json()) == 1


def test_maintenance_skips_live_execution_and_hides_expired_context(engine):
    pending = chat(engine, '中午吃了炸鸡')
    ready, release = Event(), Event()
    def hold():
        with engine.execution_locks.hold(('workflow', pending['thread_id'])):
            ready.set()
            release.wait(10)
    worker = Thread(target=hold)
    worker.start()
    assert ready.wait(2)
    try:
        with engine.repo.conn:
            engine.repo.conn.execute('UPDATE workflow_activity SET expires_at=? WHERE thread_id=?',
                ((clock.now() - timedelta(seconds=1)).isoformat(), pending['thread_id']))
        assert engine.prune_context() == 0
        assert engine.repo.workflow(pending['thread_id']) is not None
        assert engine.repo.messages() == []
        assert engine.repo.waiting_workflows() == []
    finally:
        release.set()
        worker.join(2)
    assert engine.prune_context() == 1


def test_model_extracted_substring_cannot_turn_hypothetical_into_confirmed_fact(engine):
    engine.repo.save_fact('current', '我不喜欢燕麦')
    extracted = engine.repo.save_fact('candidate', '我现在可以接受燕麦',
        context_text='如果我现在可以接受燕麦，你会怎么安排？')
    assert extracted['confirmation'] == 'candidate'
    assert engine.repo.fact('current')['lifecycle'] == 'active'
    assert [f['id'] for f in engine.repo.facts()] == ['current']


def test_independent_explicit_preference_in_mixed_question_is_still_confirmed(engine):
    saved = engine.repo.save_fact('fact', '我不喜欢燕麦', context_text='我不喜欢燕麦，今天午餐怎么吃？')
    assert saved['confirmation'] == 'confirmed'


def test_other_persons_preference_is_not_the_users_fact(engine):
    saved = engine.repo.save_fact('fact', '不喜欢燕麦', context_text='我的朋友不喜欢燕麦')
    assert saved['confirmation'] == 'candidate'
    assert engine.repo.facts() == []


def test_unconfirmed_candidate_does_not_prevent_expired_conflict_from_resolving(engine, monkeypatch):
    today = clock.now()
    engine.repo.save_fact('original', '我不喜欢燕麦')
    engine.repo.save_fact('candidate', '我现在可以接受燕麦', context_text='如果我现在可以接受燕麦呢？')
    engine.repo.save_fact('temporary', '我今天可以接受燕麦')
    monkeypatch.setattr(clock, 'now', lambda: today + timedelta(days=1))
    assert [fact['id'] for fact in engine.repo.facts()] == ['original']


def test_reading_fact_expiry_does_not_commit_an_outer_plan_transaction(engine, monkeypatch):
    engine.repo.save_fact('preference', '我不喜欢燕麦')
    draft = plan_draft(engine.repo, TARGETS)
    original = engine.repo.read_stamp
    def fail_after_fact_reads(reads):
        original(reads)
        raise RuntimeError('failed after dependency reads')
    monkeypatch.setattr(engine.repo, 'read_stamp', fail_after_fact_reads)
    with pytest.raises(RuntimeError, match='dependency reads'):
        engine.repo.save_plan('atomic-plan', 'atomic-plan', draft)
    assert engine.repo.conn.execute('SELECT 1 FROM plans WHERE id=?', ('atomic-plan',)).fetchone() is None
    assert engine.repo.conn.execute("SELECT 1 FROM provenance_nodes WHERE object_type='plan' AND object_id=?",
                                    ('atomic-plan',)).fetchone() is None
