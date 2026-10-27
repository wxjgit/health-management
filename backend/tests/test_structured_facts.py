"""Behavioral coverage for dated facts, protection, boundaries and privacy."""

from app.common.errors import ConflictError, DomainValidationError, ModelOutputError
import json
from datetime import timedelta

import pytest
import httpx
from fastapi.testclient import TestClient

from app.common import clock
from app.common.config import Settings
from app.contracts.facts import FactInput
from app.health.advice import build_advice_context
from app.health.facts import parse_fact
from app.health.planning import plan_draft
from app.health.safety import local_safety
from app.main import create_app
from app.modeling.payloads import validate_advice_payload
from app.privacy.provenance import capture
from app.workflows.engine import Engine

TARGETS = {'kcal': 1800, 'protein_g': 100, 'carbs_g': 215, 'fat_g': 60, 'exercise_minutes': 120}


@pytest.fixture
def engine(tmp_path):
    runtime = Engine(Settings(data_dir=tmp_path, scheduler_enabled=False))
    yield runtime
    runtime.close()


def test_verb_boundary_matches_food_in_both_wordings(engine):
    engine.repo.save_fact('old', '我不喜欢吃燕麦')
    new = engine.repo.save_fact('new', '现在可以接受燕麦')
    assert new['supersedes'] == 'old'
    assert engine.repo.facts()[0]['subject_key'] == '燕麦'
    assert [r['id'] for r in engine.repo.facts()] == ['new']


def test_confirmed_temporary_cover_restores_baseline_after_expiry(engine, monkeypatch):
    today = clock.now()
    baseline = engine.repo.save_fact('base', '我不喜欢燕麦')
    temporary = engine.repo.save_fact('temp', '我今天可以接受燕麦')
    chosen = engine.repo.decide_fact('temp', 'confirm', temporary['version'], 'temporary', {'base': baseline['version']})
    assert chosen['overrides'] == ['base']
    assert [r['id'] for r in engine.repo.facts()] == ['temp']
    monkeypatch.setattr(clock, 'now', lambda: today + timedelta(days=1))
    assert [r['id'] for r in engine.repo.facts()] == ['base']
    assert engine.repo.fact('base')['lifecycle'] == 'active'


def test_retracting_temporary_cover_restores_baseline(engine):
    engine.repo.save_fact('base', '我不喜欢燕麦')
    temp = engine.repo.save_fact('temp', '我今天可以接受燕麦')
    chosen = engine.repo.decide_fact('temp', 'confirm', temp['version'])
    engine.repo.decide_fact('temp', 'retract', chosen['version'])
    assert [r['id'] for r in engine.repo.facts()] == ['base']


@pytest.mark.parametrize('text', ['我暂时不吃燕麦', '我最近喜欢燕麦', '我这两周可以接受燕麦'])
def test_ambiguous_temporary_period_is_not_permanent(engine, text):
    record = engine.repo.save_fact('temp', text)
    assert record['time_scope'] == 'temporary_unknown'
    assert record['confirmation'] == 'candidate'
    assert engine.repo.facts() == []
    with pytest.raises(DomainValidationError):
        engine.repo.decide_fact('temp', 'confirm', record['version'])


def test_explicit_relative_period_is_anchored_before_replay(engine, monkeypatch):
    today = clock.now()
    saved = engine.repo.save_fact('relative', '我未来两周可以接受燕麦')
    assert saved['valid_until'] == (today.date() + timedelta(days=13)).isoformat()
    monkeypatch.setattr(clock, 'now', lambda: today + timedelta(days=2))
    replay = engine.repo.save_fact('relative', '我未来两周可以接受燕麦')
    assert replay['valid_until'] == saved['valid_until']
    assert replay['version'] == saved['version']


def test_disjoint_facts_are_resolved_for_each_plan_day(engine):
    tomorrow = clock.now().date() + timedelta(days=1)
    engine.repo.save_fact('day1', '燕麦可以接受', structured=FactInput(
        content='燕麦可以接受', type='food_preference', subject='燕麦', value='accept',
        valid_from=tomorrow, valid_until=tomorrow))
    engine.repo.save_fact('day2', '燕麦不喜欢', structured=FactInput(
        content='燕麦不喜欢', type='food_preference', subject='燕麦', value='dislike',
        valid_from=tomorrow + timedelta(days=1), valid_until=tomorrow + timedelta(days=1)))
    draft = plan_draft(engine.repo, TARGETS)
    assert draft['schedule'][0]['facts'][0]['value'] == 'accept'
    assert draft['schedule'][1]['facts'][0]['value'] == 'dislike'
    assert draft['schedule'][2]['facts'] == []


def test_future_permanent_changes_are_ordered_by_effective_date(engine, monkeypatch):
    today = clock.now()
    engine.repo.save_fact('base', '我不喜欢燕麦')
    for key, offset, value in [('later', 7, 'like'), ('earlier', 2, 'accept')]:
        engine.repo.save_fact(key, '燕麦' + ('喜欢' if value == 'like' else '可以接受'), structured=FactInput(
            content='燕麦' + ('喜欢' if value == 'like' else '可以接受'), type='food_preference', subject='燕麦',
            value=value, valid_from=(today + timedelta(days=offset)).date()))
    assert [r['id'] for r in engine.repo.facts()] == ['base']
    assert [r['id'] for r in engine.repo.resolve_facts((today + timedelta(days=3)).date())['effective']] == ['earlier']
    assert [r['id'] for r in engine.repo.resolve_facts((today + timedelta(days=8)).date())['effective']] == ['later']
    monkeypatch.setattr(clock, 'now', lambda: today + timedelta(days=8))
    assert [r['id'] for r in engine.repo.facts()] == ['later']


def test_new_current_preference_does_not_cancel_scheduled_update(engine):
    tomorrow = clock.now().date() + timedelta(days=1)
    engine.repo.save_fact('base', '我不喜欢燕麦')
    engine.repo.save_fact('future', '燕麦喜欢', structured=FactInput(
        content='燕麦喜欢', type='food_preference', subject='燕麦', value='like', valid_from=tomorrow))
    engine.repo.save_fact('current', '我现在可以接受燕麦')
    assert [r['id'] for r in engine.repo.facts()] == ['current']
    assert [r['id'] for r in engine.repo.resolve_facts(tomorrow)['effective']] == ['future']


def test_protective_fact_is_never_superseded_by_acceptance(engine):
    allergy = engine.repo.save_fact('allergy', '我对花生过敏', structured=FactInput(
        content='我对花生过敏', type='allergy', subject='花生', subject_kind='food', value='allergic'))
    engine.repo.save_fact('accept', '我现在可以接受花生')
    assert engine.repo.fact('allergy')['lifecycle'] == 'active'
    assert local_safety(engine.repo)['category'] == 'medical'
    with pytest.raises(ConflictError):
        engine.repo.save_fact('forged', '我喜欢花生', structured=FactInput(
            content='我喜欢花生', type='food_preference', subject='花生', value='like', supersedes=allergy['id']))


def test_ambiguous_edit_cannot_remove_existing_protection(engine):
    initial = engine.repo.save_fact('allergy', '我对花生过敏', structured=FactInput(
        content='我对花生过敏', type='allergy', subject='花生', value='allergic'))
    with pytest.raises(DomainValidationError):
        engine.repo.save_fact('allergy', '我对花生是否过敏', structured=FactInput(
            content='我对花生是否过敏', type='allergy', subject='花生', value='uncertain'),
            expected_version=initial['version'])
    assert engine.repo.fact('allergy')['version'] == initial['version']
    assert local_safety(engine.repo)['category'] == 'medical'


def test_reason_cannot_downgrade_clinical_content(engine):
    with pytest.raises(DomainValidationError):
        engine.repo.save_fact('forged', '我对花生过敏', structured=FactInput(
            content='我对花生过敏', type='food_preference', subject='花生', value='accept'))
    saved = engine.repo.save_fact('restriction', '我不吃花生', structured=FactInput(
        content='我不吃花生', type='restriction', subject='花生', value='avoid', restriction_reason='allergy'))
    assert saved['enforcement'] == 'clinical_review'


def test_metadata_cannot_contradict_clear_content(engine):
    with pytest.raises(DomainValidationError):
        engine.repo.save_fact('bad', '我不喜欢燕麦', structured=FactInput(
            content='我不喜欢燕麦', type='food_preference', subject='燕麦', value='accept'))
    with pytest.raises(DomainValidationError):
        engine.repo.save_fact('bad2', '我对花生过敏', structured=FactInput(
            content='我对花生过敏', type='allergy', subject='花生', value='allergic', reported_severity='severe'))


def test_professional_source_is_user_reported_and_not_sent_to_model(engine):
    advice = engine.repo.save_fact('doctor', '医生建议限制燕麦', structured=FactInput(
        content='医生建议限制燕麦', type='professional_advice', subject='燕麦', value='limit',
        valid_until=clock.now().date(), source_details={
            'professional': '某医生', 'institution': '某医院', 'document': '私人检查报告',
            'advised_on': clock.now().date()}))
    assert advice['source_kind'] == 'user_reported_professional'
    context, _ = build_advice_context(engine.repo, '今天吃饭注意什么', view='diet_today')
    wire = json.dumps(context, ensure_ascii=False)
    assert '某医生' not in wire and '某医院' not in wire and '私人检查报告' not in wire
    assert context['constraints'][0]['enforcement'] == 'clinical_review'
    validate_advice_payload(context)


def test_unknown_severity_and_expired_clinical_fact_remain_protective(engine):
    engine.repo.save_fact('condition', '我有糖尿病', structured=FactInput(
        content='我有糖尿病', type='health_condition', subject='糖尿病', value='present',
        valid_until=clock.now().date() - timedelta(days=1)))
    view = engine.repo.resolve_facts()
    assert view['constraints'][0]['reported_severity'] == 'unknown'
    assert view['constraints'][0]['lifecycle'] == 'needs_review'
    assert local_safety(engine.repo)['category'] == 'medical'


def test_more_than_budget_constraints_block_context_and_plans(engine):
    for number in range(9):
        text = f'我不吃食物{number}'
        engine.repo.save_fact(str(number), text)
    assert local_safety(engine.repo)['category'] == 'uncertain'
    with pytest.raises(DomainValidationError):
        build_advice_context(engine.repo, '今天吃饭注意什么', view='diet_today')
    with pytest.raises(DomainValidationError):
        plan_draft(engine.repo, TARGETS)


def test_model_draft_requires_evidence_and_does_not_choose_existing_ids(engine):
    with pytest.raises(ModelOutputError):
        engine.repo.save_fact('model', '我不喜欢燕麦', draft={
            'type': 'food_preference', 'subject': '燕麦', 'value': 'dislike'})
    from app.contracts.facts import FactDraft
    with pytest.raises(ValueError):
        FactDraft(type='food_preference', subject='燕麦', value='dislike', supersedes='forged')
    parsed = parse_fact('我不喜欢吃燕麦').model_dump(mode='json', exclude={'content', 'source_kind', 'supersedes'})
    record = engine.repo.save_fact('model', '我不喜欢吃燕麦', draft=parsed)
    assert record['confirmation'] == 'confirmed'


def test_hypothesis_cannot_be_promoted_without_edit(engine):
    saved = engine.repo.save_fact('maybe', '我喜欢燕麦', context_text='如果我喜欢燕麦呢？')
    with pytest.raises(DomainValidationError):
        engine.repo.decide_fact('maybe', 'confirm', saved['version'])
    assert local_safety(engine.repo)['category'] == 'general'


def test_repeated_manual_input_deduplicates_after_server_anchors_start_date(engine):
    payload = FactInput(content='燕麦喜欢', type='food_preference', subject='燕麦', value='like')
    first = engine.repo.save_fact('one', payload.content, structured=payload)
    second = engine.repo.save_fact('two', payload.content, structured=payload)
    assert first['id'] == second['id']


def test_conflict_resolution_rejects_changed_peers(engine):
    base = engine.repo.save_fact('base', '我不喜欢燕麦')
    temp = engine.repo.save_fact('temp', '我今天可以接受燕麦')
    engine.repo.save_fact('base', '我喜欢燕麦', expected_version=base['version'])
    with pytest.raises(ConflictError):
        engine.repo.decide_fact('temp', 'confirm', temp['version'], 'temporary', {'base': base['version']})


def test_delete_cover_restores_base_and_delete_base_redacts_links(engine):
    engine.repo.save_fact('base', '我不喜欢燕麦')
    temp = engine.repo.save_fact('temp', '我今天可以接受燕麦')
    selected = engine.repo.decide_fact('temp', 'confirm', temp['version'])
    assert engine.delete_fact('base')['deleted']
    assert engine.repo.fact('temp')['overrides'] == []
    assert all('base' not in r.get('overrides', []) for r in engine.repo.fact_revisions('temp'))
    assert engine.repo.fact('temp')['version'] == selected['version']
    assert engine.delete_fact('temp')['deleted']


def test_delete_temporary_fact_restores_baseline(engine):
    engine.repo.save_fact('base', '我不喜欢燕麦')
    temp = engine.repo.save_fact('temp', '我今天可以接受燕麦')
    engine.repo.decide_fact('temp', 'confirm', temp['version'])
    assert engine.delete_fact('temp')['deleted']
    assert [r['id'] for r in engine.repo.facts()] == ['base']


def test_aliases_are_scoped_and_do_not_merge_mixed_foods(engine):
    engine.repo.save_fact('peanut', '我不喜欢花生米')
    engine.repo.save_fact('accept', '我可以接受花生')
    engine.repo.save_fact('butter', '我不喜欢花生酱')
    assert engine.repo.fact('peanut')['lifecycle'] == 'superseded'
    assert {r['subject_key'] for r in engine.repo.facts()} == {'花生', '花生酱'}


def test_v6_migration_keeps_legacy_details_and_snapshots(engine):
    with engine.repo.conn:
        engine.repo.conn.execute('INSERT INTO facts VALUES (?,?,?,?,?,?)',
                                ('v6', '医生要求控制饮食', 'user', None, 'confirmed', clock.stamp()))
        legacy = {'type': 'legacy_text', 'source_kind': 'legacy', 'version': 1, 'schema_version': 1}
        engine.repo.conn.execute('INSERT INTO fact_details VALUES (?,?)', ('v6', json.dumps(legacy)))
        engine.repo.conn.execute('PRAGMA user_version=6')
    for _ in range(2):
        restarted = Engine(engine.settings)
        try:
            assert restarted.repo.conn.execute('PRAGMA user_version').fetchone()[0] == 7
            record = restarted.repo.fact('v6')
            assert record['content'] == '医生要求控制饮食'
            assert record['source_kind'] == 'legacy'
            assert record['reported_severity'] == 'unknown'
            assert record['enforcement'] == 'clinical_review'
            stored = json.loads(restarted.repo.conn.execute('SELECT data FROM fact_details WHERE fact_id=?', ('v6',)).fetchone()[0])
            assert stored == legacy
        finally:
            restarted.close()


def test_model_fact_draft_is_preserved_through_real_chat_workflow(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, scheduler_enabled=False, model_base_url='https://test.invalid/v1',
                        model_name='test', model_api_key='test-only')
    original = httpx.Client
    text = '我不喜欢吃燕麦'
    draft = parse_fact(text).model_dump(mode='json', exclude={'content', 'source_kind', 'supersedes'})

    def handler(request):
        data = json.loads(json.loads(request.content)['messages'][1]['content'])
        if data['task'] == 'safety_classification':
            output = {'findings': [], 'uncertain': False}
        else:
            assert data['task'] == 'processing_plan'
            output = {'actions': [{'intent': 'fact', 'text': text, 'fact_text': text, 'fact_draft': draft}]}
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(output)}}]})

    monkeypatch.setattr('app.modeling.parser.httpx.Client',
                        lambda *a, **kw: original(*a, **kw, transport=httpx.MockTransport(handler)))
    with TestClient(create_app(settings)) as client:
        result = client.post('/api/chat', json={'request_id': 'structured-chat', 'text': text})
        assert result.status_code == 200, result.text
        record = client.get('/api/facts').json()[0]
        assert record['subject_key'] == '燕麦'
        assert record['schema_version'] == 2
        assert record['evidence_message_id']
        assert record['evidence'][0]['quote'] == '燕麦'


def test_midnight_invalidates_pending_plan_even_with_same_fact_versions(engine, monkeypatch):
    today = clock.now()
    with capture(engine.repo):
        draft = plan_draft(engine.repo, TARGETS)
    engine.repo.save_plan('plan', 'plan-op', draft)
    monkeypatch.setattr(clock, 'now', lambda: today + timedelta(days=1))
    assert engine.repo.decide_plan('plan', True) == 'stale'


def test_api_accepts_sources_and_rejects_client_owned_risk(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path, scheduler_enabled=False))) as client:
        response = client.post('/api/facts', json={'content': '医生建议限制燕麦',
            'type': 'professional_advice', 'subject': '燕麦', 'value': 'limit',
            'source_details': {'professional': '某医生', 'advised_on': '2026-10-20'}})
        assert response.status_code == 200, response.text
        assert response.json()['source_details']['advised_on'] == '2026-10-20'
        assert response.json()['schema_version'] == 2
        assert client.post('/api/facts', json={'content': '我对花生过敏',
            'enforcement': 'soft'}).status_code == 422
