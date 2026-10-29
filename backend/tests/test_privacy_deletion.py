"""Privacy deletion must erase copies while preserving independently usable state."""

import json
import copy
from datetime import timedelta, date
from uuid import uuid4

import pytest
from authenticated_client import AuthenticatedClient as TestClient

from app.common.clock import now
from app.common.config import Settings
from app.evidence import assert_grounded
from app.health import review, plan_draft
from app.main import create_app
from app.privacy.deletion import DeletionError
from app.privacy.provenance import capture
from app.workflows.concurrency import RequestErased
from app.workflows.engine import Engine


TARGETS = {'kcal': 1800, 'protein_g': 100, 'carbs_g': 215, 'fat_g': 60, 'exercise_minutes': 120}


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, scheduler_enabled=False)


@pytest.fixture
def engine(settings):
    runtime = Engine(settings, user_id='00000000-0000-4000-8000-000000000001')
    yield runtime
    runtime.close()


def chat(engine, text, request_id=None, conversation='main'):
    initial = {'input_text': text, 'processing_mode': 'local_only'}
    return engine.run('chat', request_id or str(uuid4()), initial, initial, conversation)


def erase(engine, kind, target):
    preview = engine.deletions.preview(kind, target)
    result = engine.deletions.delete(kind, target, preview_id=preview['preview_id'])
    assert result['state'] == 'completed', result
    return result


def checkpoint_threads(engine):
    return {item.config['configurable']['thread_id'] for item in engine.checkpointer.list(None)}


def test_delete_preserves_independent_messages_reports_notices_and_pending_progress(engine):
    chat(engine, '今天体重65公斤')
    target = engine.repo.events()[0]['id']
    pending = chat(engine, '今天记录体重', conversation='sleep')
    assert pending['status'] == 'waiting_input'
    review(engine.repo, 'day', now().date() - timedelta(days=2))
    engine.repo.notify('independent', '固定提醒', '记得记录', sources=[])
    before_messages = engine.repo.messages('sleep')
    before_result = engine.repo.workflow(pending['thread_id'])
    erase(engine, 'event', target)
    assert engine.repo.messages('sleep') == before_messages
    assert engine.repo.workflow(pending['thread_id']) == before_result
    assert pending['thread_id'] in checkpoint_threads(engine)
    assert len(engine.repo.reviews()) == 1
    assert engine.repo.notifications()[0]['dedupe_key'] == 'independent'
    result = engine.resume(pending['thread_id'], {
        'request_id': str(uuid4()), 'interrupt_id': pending['pending_input']['interrupt_id'], 'text': '66公斤',
    })
    assert result['status'] == 'completed'


def test_shared_source_is_removed_from_latest_event_and_every_revision(engine):
    chat(engine, '今天体重65公斤，轻松跑步20分钟')
    weight = next(event for event in engine.repo.events() if event['kind'] == 'weight')
    exercise = next(event for event in engine.repo.events() if event['kind'] == 'exercise')
    assert '65公斤' not in json.dumps(exercise['data']['evidence_sources'], ensure_ascii=False)
    assert '20分钟' not in json.dumps(weight['data']['evidence_sources'], ensure_ascii=False)
    engine.repo.save_events([exercise['data']], 'exercise-second-version', correction_id=exercise['id'])
    result = erase(engine, 'event', weight['id'])
    assert result['counts']['event:minimize'] == 1
    survivor = engine.repo.event(exercise['id'])
    assert survivor['data']['value'] == 20
    assert_grounded(survivor['data'])
    payloads = [survivor['data']] + [json.loads(row[0]) for row in engine.repo.conn.execute(
        'SELECT data FROM revisions WHERE event_id=?', (exercise['id'],),
    )]
    assert all('65公斤' not in json.dumps(data, ensure_ascii=False) for data in payloads)
    assert '65公斤' not in json.dumps(engine.repo.export(), ensure_ascii=False)


def test_unknown_shared_record_requires_explicit_group_preview(engine):
    chat(engine, '今天体重65公斤，轻松跑步20分钟')
    weight = next(event for event in engine.repo.events() if event['kind'] == 'weight')
    exercise = next(event for event in engine.repo.events() if event['kind'] == 'exercise')
    with engine.repo.conn:
        data = {**exercise['data'], 'evidence': []}
        engine.repo.conn.execute('UPDATE revisions SET data=? WHERE event_id=?', (json.dumps(data), exercise['id']))
    with pytest.raises(DeletionError) as error:
        engine.delete_event(weight['id'])
    assert error.value.code == 'preview_required'
    assert len(engine.repo.events()) == 2
    result = erase(engine, 'event', weight['id'])
    assert result['scope'] == 'shared_source'
    assert engine.repo.events() == []


def test_deleted_original_and_resume_requests_cannot_recreate_content_after_restart(settings):
    runtime = Engine(settings, user_id='00000000-0000-4000-8000-000000000001')
    request_id = str(uuid4())
    pending = chat(runtime, '今天记录体重', request_id)
    payload = {'request_id': str(uuid4()), 'interrupt_id': pending['pending_input']['interrupt_id'], 'text': '65公斤'}
    runtime.resume(pending['thread_id'], payload)
    erase(runtime, 'event', runtime.repo.events()[0]['id'])
    runtime.close()
    runtime = Engine(settings, user_id='00000000-0000-4000-8000-000000000001')
    try:
        with pytest.raises(RequestErased):
            chat(runtime, '今天记录体重', request_id)
        with pytest.raises(RequestErased):
            runtime.resume(pending['thread_id'], payload)
        assert runtime.repo.events() == []
        assert runtime.repo.messages() == []
        assert checkpoint_threads(runtime) == set()
    finally:
        runtime.close()


def test_missing_target_and_repeated_delete_have_no_independent_side_effect(engine):
    chat(engine, '今天体重65公斤')
    before = engine.repo.messages()
    with pytest.raises(DeletionError) as error:
        engine.delete_fact('does-not-exist')
    assert error.value.status == 404
    assert engine.repo.messages() == before
    target = engine.repo.events()[0]['id']
    result = erase(engine, 'event', target)
    pending = chat(engine, '今天记录体重', conversation='other')
    assert engine.delete_event(target)['deletion_id'] == result['deletion_id']
    assert engine.repo.workflow(pending['thread_id'])


def test_preview_rejects_new_transitive_copy_without_deleting_anything(engine):
    chat(engine, '今天体重65公斤')
    target = engine.repo.events()[0]['id']
    preview = engine.deletions.preview('event', target)
    with capture(engine.repo):
        event = engine.repo.event(target)
        engine.repo.notify('late-copy', '新增副本', event['raw_text'])
    with pytest.raises(DeletionError) as error:
        engine.delete_event(target, preview['preview_id'])
    assert error.value.code == 'scope_changed'
    assert error.value.preview['counts']['notification:delete'] == 1
    assert engine.repo.event(target)
    assert engine.repo.notifications()


def test_plan_tracks_sources_beyond_twelve_display_refs_and_preserves_manual_targets(engine):
    target = None
    for index in range(15):
        ids = engine.repo.save_events([{
            'kind': 'exercise', 'value': index + 10, 'occurred_at': (now() - timedelta(days=index)).isoformat(),
            'raw_text': f'运动{index + 10}分钟',
        }], f'seed:{index}')
        if index == 14:
            target = ids[0]
    proposal = engine.run('plan', str(uuid4()), {}, {'plan': {'targets': TARGETS}})
    engine.resume(proposal['thread_id'], {
        'request_id': str(uuid4()), 'interrupt_id': proposal['pending_input']['interrupt_id'], 'approved': True,
    })
    plan = engine.repo.active_plan()
    assert target not in [item['id'] for item in plan['data']['evidence_refs']]
    assert target in plan['data']['source_event_ids']
    erase(engine, 'event', target)
    survivor = engine.repo.active_plan()
    assert survivor['data']['targets'] == TARGETS
    assert survivor['data']['schedule'] == []
    assert survivor['data']['evidence_refs'] == []


def test_transitive_old_insight_generation_reaches_review_and_notification(engine):
    chat(engine, '今天体重65公斤')
    target = engine.repo.events()[0]['id']
    with capture(engine.repo):
        engine.repo.event(target)
        insight_id = engine.repo.save_insight('copy', {'description': '体重65公斤'})
    review(engine.repo, 'day', now().date() - timedelta(days=30))
    review_id = engine.repo.reviews()[0]['id']
    with engine.repo.conn:
        engine.repo.notify('copy-review', '复盘副本', '体重65公斤', sources=[engine.repo.provenance_node('review', review_id)])
    # Replacing an insight does not erase dependency edges of its old generation.
    with capture(engine.repo):
        engine.repo.save_insight('copy', {'description': '新的独立结果'})
    erase(engine, 'event', target)
    assert engine.repo.reviews() == []
    assert engine.repo.notifications() == []
    assert engine.repo.insights()[0]['id'] == insight_id
    assert engine.repo.insights()[0]['data']['description'] == '新的独立结果'


def test_checkpoint_failure_is_pending_and_startup_recovers(settings, monkeypatch):
    runtime = Engine(settings, user_id='00000000-0000-4000-8000-000000000001')
    chat(runtime, '今天体重65公斤')
    target = runtime.repo.events()[0]['id']
    original = runtime.checkpointer.delete_thread
    monkeypatch.setattr(runtime.checkpointer, 'delete_thread', lambda _: (_ for _ in ()).throw(OSError('checkpoint busy')))
    result = runtime.delete_event(target)
    assert result['state'] == 'pending'
    assert result['deleted'] is False
    assert runtime.repo.events() == []
    assert runtime.repo.messages() == []
    monkeypatch.setattr(runtime.checkpointer, 'delete_thread', original)
    runtime.close()
    runtime = Engine(settings, user_id='00000000-0000-4000-8000-000000000001')
    try:
        assert runtime.deletions.status(result['deletion_id'])['state'] == 'completed'
        assert checkpoint_threads(runtime) == set()
    finally:
        runtime.close()


def test_business_failure_keeps_durable_fence_and_restart_finishes(settings, monkeypatch):
    runtime = Engine(settings, user_id='00000000-0000-4000-8000-000000000001')
    chat(runtime, '今天体重65公斤')
    target = runtime.repo.events()[0]['id']
    monkeypatch.setattr(runtime.deletions, '_erase_business', lambda *_: (_ for _ in ()).throw(OSError('busy')))
    result = runtime.delete_event(target)
    assert result['state'] == 'pending'
    assert runtime.repo.conn.execute('SELECT state FROM deletion_jobs').fetchone()[0] == 'business_pending'
    runtime.close()
    runtime = Engine(settings, user_id='00000000-0000-4000-8000-000000000001')
    try:
        assert runtime.repo.events() == []
        assert runtime.repo.messages() == []
        assert runtime.deletions.status(result['deletion_id'])['state'] == 'completed'
    finally:
        runtime.close()


def test_compaction_failure_does_not_report_completion(engine, monkeypatch):
    chat(engine, '今天体重65公斤')
    target = engine.repo.events()[0]['id']
    original = engine.repo.compact
    monkeypatch.setattr(engine.repo, 'compact', lambda: (_ for _ in ()).throw(OSError('WAL busy')))
    result = engine.delete_event(target)
    assert result['state'] == 'pending'
    monkeypatch.setattr(engine.repo, 'compact', original)
    assert engine.deletions.status(result['deletion_id'], retry=True)['state'] == 'completed'


def test_legacy_context_is_unknown_and_requires_preview(settings):
    runtime = Engine(settings, user_id='00000000-0000-4000-8000-000000000001')
    chat(runtime, '今天体重65公斤')
    target = runtime.repo.events()[0]['id']
    with runtime.repo.conn:
        runtime.repo.conn.execute("INSERT INTO notifications VALUES ('legacy','legacy','旧通知','可能的原文副本',0,'2020')")
    runtime.close()
    runtime = Engine(settings, user_id='00000000-0000-4000-8000-000000000001')
    try:
        preview = runtime.deletions.preview('event', target)
        assert preview['scope'] == 'legacy_context'
        with pytest.raises(DeletionError) as error:
            runtime.delete_event(target)
        assert error.value.code == 'preview_required'
        erase(runtime, 'event', target)
        assert runtime.repo.notifications() == []
    finally:
        runtime.close()


def test_archive_is_atomic_with_aggregate_and_explicit_delete_removes_contribution(engine):
    ids = engine.repo.save_events([{
        'kind': 'weight', 'value': 65, 'occurred_at': '2020-01-02T12:00:00+08:00', 'raw_text': '旧体重65公斤',
    }], 'old-weight')
    assert engine.archive(date(2026, 10, 26)) == ['2020-01']
    assert engine.repo.events() == []
    assert engine.repo.summaries()[0]['data']['avg_weight_kg'] == 65
    erase(engine, 'event', ids[0])
    assert engine.repo.summaries() == []
    for row in engine.repo.conn.execute('SELECT manifest FROM deletion_jobs'):
        assert 'avg_weight_kg' not in row[0]  # No unmanaged aggregate recovery copy.


def test_api_preview_execution_and_erased_request(settings):
    with TestClient(create_app(settings)) as client:
        body = {'request_id': str(uuid4()), 'text': '今天体重65公斤', 'processing_mode': 'local_only'}
        assert client.post('/api/chat', json=body).status_code == 200
        target = client.get('/api/events').json()[0]['id']
        preview = client.post('/api/deletions/preview', json={'target_type': 'event', 'target_id': target}).json()
        payload = {'preview_id': preview['preview_id'], 'request_id': str(uuid4())}
        result = client.post('/api/deletions', json=payload)
        assert result.status_code == 200
        assert result.json()['state'] == 'completed'
        assert client.post('/api/deletions', json=payload).json()['deletion_id'] == result.json()['deletion_id']
        erased = client.post('/api/chat', json=body)
        assert erased.status_code == 410
        assert erased.json()['code'] == 'request_erased'
        assert client.delete('/api/facts/missing').status_code == 404


def test_fact_deletion_keeps_offline_boundary_and_independent_flow(engine):
    chat(engine, '我不喜欢燕麦')
    fact = engine.repo.facts()[0]
    advice = chat(engine, '下一餐怎么吃？', conversation='advice')
    pending = chat(engine, '今天记录体重', conversation='independent')
    assert advice['safety']['category'] == 'uncertain'
    assert fact['id'] not in advice['source_fact_ids']
    erase(engine, 'fact', fact['id'])
    assert engine.repo.facts() == []
    assert engine.repo.messages('advice')
    assert engine.repo.workflow(pending['thread_id'])
    assert pending['thread_id'] in checkpoint_threads(engine)
    assert '燕麦' not in json.dumps(engine.repo.export(), ensure_ascii=False)


def test_empty_query_dependency_invalidates_plan_when_matching_record_is_added(engine):
    draft = plan_draft(engine.repo, TARGETS)
    engine.repo.save_plan('empty-plan', 'empty-plan', draft)
    assert draft['evidence_refs'] == []
    engine.repo.save_events([{'kind': 'exercise', 'value': 30, 'raw_text': '跑步30分钟',
                             'occurred_at': now().isoformat()}], 'new-exercise')
    assert engine.repo.decide_plan('empty-plan', True) == 'stale'


def test_business_pending_api_reads_are_fenced_and_retry_endpoint_finishes(settings, monkeypatch):
    with TestClient(create_app(settings)) as client:
        body = {'request_id': str(uuid4()), 'text': '今天体重65公斤', 'processing_mode': 'local_only'}
        client.post('/api/chat', json=body)
        target = client.get('/api/events').json()[0]['id']
        service = client.app.state.test_engine.deletions
        original = service._erase_business
        monkeypatch.setattr(service, '_erase_business', lambda *_: (_ for _ in ()).throw(OSError('busy')))
        preview = client.post('/api/deletions/preview', json={'target_type': 'event', 'target_id': target}).json()
        job = client.post('/api/deletions', json={'preview_id': preview['preview_id'], 'request_id': str(uuid4())}).json()
        assert job['state'] == 'pending'
        for path in ('/api/events', '/api/messages', '/api/data/export', '/api/workflows'):
            assert client.get(path).status_code == 503
        assert client.get(f"/api/deletions/{job['deletion_id']}").json()['state'] == 'pending'
        monkeypatch.setattr(service, '_erase_business', original)
        assert client.post(f"/api/deletions/{job['deletion_id']}/retry").json()['state'] == 'completed'
        assert client.get('/api/events').json() == []


def test_unmanaged_checkpoint_is_unknown_and_its_request_cannot_be_replayed(settings):
    runtime = Engine(settings, user_id='00000000-0000-4000-8000-000000000001')
    chat(runtime, '今天体重65公斤')
    target = runtime.repo.events()[0]['id']
    orphan_request = str(uuid4())
    orphan = chat(runtime, '今天记录体重', orphan_request, conversation='orphan')
    with runtime.repo.conn:
        runtime.repo.conn.execute('DELETE FROM messages WHERE thread_id=?', (orphan['thread_id'],))
        runtime.repo.conn.execute('DELETE FROM workflows WHERE id=?', (orphan['thread_id'],))
    runtime.close()
    runtime = Engine(settings, user_id='00000000-0000-4000-8000-000000000001')
    try:
        preview = runtime.deletions.preview('event', target)
        assert preview['scope'] == 'legacy_context'
        assert preview['counts']['checkpoint:delete'] == 1
        erase(runtime, 'event', target)
        assert checkpoint_threads(runtime) == set()
        with pytest.raises(RequestErased):
            chat(runtime, '今天记录体重', orphan_request)
    finally:
        runtime.close()


def test_archive_failure_rolls_back_both_aggregate_and_original_erasure(settings, monkeypatch):
    runtime = Engine(settings, user_id='00000000-0000-4000-8000-000000000001')
    runtime.repo.save_events([{'kind': 'weight', 'value': 65, 'occurred_at': '2020-01-02T12:00:00+08:00',
                              'raw_text': '旧体重65公斤'}], 'archive-fault')
    original = runtime.repo.persist_provenance

    def fail_aggregate(kind, *args, **kwargs):
        if kind == 'summary':
            raise OSError('aggregate commit fault')
        return original(kind, *args, **kwargs)

    monkeypatch.setattr(runtime.repo, 'persist_provenance', fail_aggregate)
    with pytest.raises(RuntimeError, match='归档清理尚未完成'):
        runtime.archive(date(2026, 10, 26))
    assert len(runtime.repo.events()) == 1
    assert runtime.repo.summaries() == []
    runtime.close()
    runtime = Engine(settings, user_id='00000000-0000-4000-8000-000000000001')
    try:
        assert runtime.repo.events() == []
        assert runtime.repo.summaries()[0]['data']['avg_weight_kg'] == 65
    finally:
        runtime.close()


def test_unknown_base_records_are_explicitly_listed_in_fallback(engine):
    chat(engine, '今天体重65公斤')
    target = engine.repo.events()[0]['id']
    old = engine.repo.save_events([{'kind': 'sleep', 'value': 360, 'raw_text': '缺少来源的历史原文',
                                   'occurred_at': now().isoformat()}], 'legacy-base')[0]
    with engine.repo.conn:
        engine.repo.conn.execute("UPDATE provenance_nodes SET provenance_state='unknown' WHERE object_type='event' AND object_id=?", (old,))
    preview = engine.deletions.preview('event', target)
    assert preview['scope'] == 'legacy_context'
    assert preview['counts']['event:delete'] == 2
    assert any('不能验证的记录' in reason for reason in preview['reasons'])
    with pytest.raises(DeletionError):
        engine.delete_event(target)
    assert len(engine.repo.events()) == 2


def test_sibling_field_copying_erased_value_requires_group_deletion(engine):
    chat(engine, '今天体重65公斤，轻松跑步20分钟')
    weight = next(event for event in engine.repo.events() if event['kind'] == 'weight')
    exercise = next(event for event in engine.repo.events() if event['kind'] == 'exercise')
    data = copy.deepcopy(exercise['data'])
    entry = copy.deepcopy(next(item for item in weight['data']['evidence'] if item['field'] == 'value'))
    source = next(item for item in weight['data']['evidence_sources'] if item['message_id'] == entry['source_message_id'])
    data['quality'] = entry['quote']
    entry['field'] = 'quality'
    entry['normalization'] = {'input_text': entry['quote'], 'output_value': entry['quote'],
                              'output_unit': None, 'rule_version': 'exact-text-v1'}
    data['evidence'].append(entry)
    data['evidence_sources'].append(source)
    assert_grounded(data)
    engine.repo.save_events([data], 'cross-field-copy', correction_id=exercise['id'])
    preview = engine.deletions.preview('event', weight['id'])
    assert preview['scope'] == 'shared_source'
    assert preview['counts']['event:delete'] == 2
    erase(engine, 'event', weight['id'])
    assert engine.repo.events() == []
