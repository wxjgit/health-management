from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.common.config import Settings
from app.main import create_app
from app.contracts.profile import Targets


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path, scheduler_enabled=False))) as client:
        yield client


def profile(**values):
    return {'age': 30, 'sex': 'female', 'height_cm': 170, 'weight_kg': 70,
            'goal_weight_kg': 60, 'goal_date': '2027-01-01', 'activity': 'light',
            'nutrition_context': 'general', **values}


def save_profile(client, **values):
    response = client.put('/api/profile', json=profile(**values))
    assert response.status_code == 200, response.text


def propose(client, **values):
    return client.post('/api/plans/propose', json={'request_id': str(uuid4()), **values})


def accept(client, proposal):
    response = client.post(f'/api/workflows/{proposal["thread_id"]}/resume', json={
        'request_id': str(uuid4()), 'interrupt_id': proposal['pending_input']['interrupt_id'], 'approved': True})
    assert response.status_code == 200, response.text
    return response.json()


def test_missing_profile_is_not_fabricated_and_default_read_is_non_mutating(client):
    result = client.get('/api/targets/default').json()
    assert result['status'] == 'needs_profile'
    assert result['targets'] is None
    assert client.get('/api/plans').json() == []
    assert client.get('/api/dashboard').json()['targets'] is None
    assert propose(client).status_code == 409


def test_formula_macro_conversion_and_provenance(client):
    save_profile(client)
    version = client.app.state.engine.repo.version()
    result = client.get('/api/targets/default').json()
    assert result['status'] == 'ready'
    assert result['targets'] == {'kcal': 2100, 'protein_g': 105, 'carbs_g': 262,
                                 'fat_g': 70, 'exercise_minutes': 150}
    assert result['estimate']['resting_kcal'] == 1452
    assert result['estimate']['maintenance_kcal'] == 2322
    Targets.model_validate(result['targets'])
    assert '10%' in result['basis']
    assert '产品规则' in result['basis']
    assert len(result['fingerprint']) == 64
    assert len(result['sources']) == 4
    assert client.app.state.engine.repo.version() == version
    assert client.get('/api/plans').json() == []


def test_default_draft_requires_first_confirmation_and_then_activates(client):
    save_profile(client)
    expected = client.get('/api/targets/default').json()
    response = propose(client)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['status'] == 'waiting_input'
    assert result['pending_input']['target_source'] == 'system_default'
    assert result['pending_input']['targets'] == expected['targets']
    assert client.get('/api/dashboard').json()['targets'] is None
    assert accept(client, result)['status'] == 'completed'
    active = next(p for p in client.get('/api/plans').json() if p['status'] == 'active')
    assert active['data']['targets'] == expected['targets']
    assert active['data']['target_metadata']['fingerprint'] == expected['fingerprint']
    assert client.get('/api/dashboard').json()['targets'] == expected['targets']


def test_unchanged_default_values_submitted_with_reference_are_accepted(client):
    save_profile(client)
    result = client.get('/api/targets/default').json()
    response = propose(client, targets=result['targets'], target_source='system_default',
                       suggestion_fingerprint=result['fingerprint'])
    assert response.status_code == 200, response.text
    assert response.json()['pending_input']['target_source'] == 'system_default'


def test_stale_or_modified_default_values_cannot_be_mislabeled(client):
    save_profile(client)
    old = client.get('/api/targets/default').json()
    save_profile(client, age=31)
    stale = propose(client, targets=old['targets'], target_source='system_default',
                    suggestion_fingerprint=old['fingerprint'])
    assert stale.status_code == 409
    assert '基础资料或限制已变化' in stale.json()['detail']
    fresh = client.get('/api/targets/default').json()
    modified = {**fresh['targets'], 'exercise_minutes': 180}
    assert propose(client, targets=modified, target_source='system_default',
                   suggestion_fingerprint=fresh['fingerprint']).status_code == 409
    assert propose(client, targets=modified, target_source='manual').status_code == 200
    assert propose(client, targets=fresh['targets'], target_source='system_default').status_code == 422


def test_profile_changed_after_proposal_invalidates_confirmation(client):
    save_profile(client)
    pending = propose(client).json()
    save_profile(client, weight_kg=75)
    result = accept(client, pending)
    assert '过期' in result['reply']
    assert client.get('/api/dashboard').json()['targets'] is None


def test_custom_target_is_not_overwritten_when_profile_or_default_changes(client):
    save_profile(client)
    manual = {'kcal': 1800, 'protein_g': 100, 'carbs_g': 215, 'fat_g': 60, 'exercise_minutes': 120}
    accept(client, propose(client, targets=manual).json())
    save_profile(client, weight_kg=75)
    assert client.get('/api/targets/default').json()['targets'] != manual
    assert client.get('/api/dashboard').json()['targets'] == manual
    regenerated = propose(client).json()
    assert regenerated['status'] == 'completed'
    assert client.get('/api/dashboard').json()['targets'] == manual
    current = next(p for p in client.get('/api/plans').json() if p['status'] == 'active')
    assert current['data']['target_source'] == 'manual'
    restored = propose(client, target_source='system_default').json()
    assert restored['status'] == 'waiting_input'
    assert restored['pending_input']['targets'] == client.get('/api/targets/default').json()['targets']
    assert client.get('/api/dashboard').json()['targets'] == manual


@pytest.mark.parametrize('activity,factor', [('sedentary', 1.4), ('light', 1.6), ('moderate', 1.8), ('active', 2.0)])
def test_activity_bins_are_explicit_product_policy(client, activity, factor):
    save_profile(client, activity=activity)
    result = client.get('/api/targets/default').json()
    assert result['estimate']['activity_factor'] == factor


def test_goal_deadline_never_changes_energy_estimate(client):
    save_profile(client, goal_date='2026-10-11')
    early = client.get('/api/targets/default').json()
    save_profile(client, goal_date='2028-01-01')
    late = client.get('/api/targets/default').json()
    assert early['targets'] == late['targets']
    assert early['fingerprint'] != late['fingerprint']


def test_maintenance_and_weight_gain_do_not_create_weight_loss_or_surplus(client):
    save_profile(client, goal_weight_kg=70)
    maintain = client.get('/api/targets/default').json()
    assert maintain['targets']['kcal'] == 2300
    save_profile(client, goal_weight_kg=75)
    gaining = client.get('/api/targets/default').json()
    assert gaining['targets'] == maintain['targets']
    assert any('不自动制定增重' in w for w in gaining['warnings'])


@pytest.mark.parametrize('values', [
    {'age': 17}, {'age': 18}, {'age': 79}, {'sex': 'other'},
    {'nutrition_context': 'pregnant'}, {'nutrition_context': 'breastfeeding'}, {'nutrition_context': 'clinical'},
    {'weight_kg': 45}, {'goal_weight_kg': 45}, {'weight_kg': 120},
    {'age': 75, 'height_cm': 150, 'weight_kg': 45, 'goal_weight_kg': 45, 'activity': 'sedentary'},
])
def test_unsupported_profiles_do_not_receive_automatic_targets(client, values):
    save_profile(client, **values)
    result = client.get('/api/targets/default').json()
    assert result['status'] == 'unavailable'
    assert result['targets'] is None
    assert result['reason']
    response = propose(client)
    if values.get('age', 30) < 18 or values.get('nutrition_context') in ('pregnant', 'breastfeeding', 'clinical'):
        assert response.status_code == 200
        assert response.json()['safety']['category'] != 'general'
    else:
        assert response.status_code == 409


def test_known_medical_constraint_blocks_default_and_manual_plan_generation(client):
    save_profile(client)
    client.app.state.engine.repo.save_fact('restriction', '医生要求限制蛋白质')
    assert client.get('/api/targets/default').json()['targets'] is None
    manual = {'kcal': 1800, 'protein_g': 70, 'carbs_g': 245, 'fat_g': 60, 'exercise_minutes': 0}
    result = propose(client, targets=manual).json()
    assert result['safety']['category'] == 'medical'
    assert client.get('/api/plans').json() == []


def test_floor_reduces_deficit_instead_of_forcing_low_energy(client):
    save_profile(client, age=55, height_cm=155, weight_kg=55, goal_weight_kg=50, activity='sedentary')
    result = client.get('/api/targets/default').json()
    assert result['status'] == 'ready'
    assert result['targets']['kcal'] >= 1500
    assert any('缩小或取消' in w for w in result['warnings'])


def test_male_coefficient_and_bounded_deficit(client):
    save_profile(client, sex='male', height_cm=175, weight_kg=80, goal_weight_kg=70)
    result = client.get('/api/targets/default').json()
    assert result['targets']['kcal'] == 2550
    assert result['estimate']['resting_kcal'] == 1749


def test_unspecified_suitability_is_not_claimed_to_be_known(client):
    save_profile(client, nutrition_context='unspecified')
    result = client.get('/api/targets/default').json()
    assert any('适用情况尚未说明' in w for w in result['warnings'])


def test_incomplete_legacy_profile_returns_actionable_reason(client):
    client.app.state.engine.repo.update_profile({'age': 30})
    result = client.get('/api/targets/default').json()
    assert result['status'] == 'needs_profile'
    assert result['targets'] is None


def test_rounding_never_enlarges_the_product_deficit_cap(client):
    save_profile(client, weight_kg=95, height_cm=180, activity='moderate')
    result = client.get('/api/targets/default').json()
    assert result['status'] == 'ready'
    assert result['estimate']['deficit_kcal'] <= 300
