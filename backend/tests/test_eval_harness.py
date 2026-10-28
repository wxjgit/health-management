"""The evaluation harness is isolated, strict and safe to use in CI."""

import json
from pathlib import Path

import pytest

from app.common.config import ROOT
from evals import EvaluationRunner, load_scenarios
from evals.reports import write_report


CASES = Path(__file__).resolve().parents[1] / 'evals' / 'cases'


def test_bundled_scenarios_grade_state_calls_replay_restart_and_fail_closed(tmp_path):
    scenario_set = load_scenarios(CASES)
    report = EvaluationRunner(tmp_path / 'isolated-runs').run(scenario_set)

    assert report['summary'] == {
        'scenario_count': 5,
        'trial_count': 5,
        'passed_trials': 5,
        'pass_at_1': 1.0,
        'pass_power_k': 1.0,
    }
    rows = {row['scenario_id']: row for row in report['trials']}
    assert rows['mock_model_weight_boundary']['model_calls_by_purpose'] == {
        'processing_plan': 1, 'safety_classification': 1,
    }
    assert rows['mock_weight_resume_after_restart']['model_calls_by_purpose'] == {
        'patch_draft': 1, 'processing_plan': 1, 'safety_classification': 2,
    }
    assert rows['classifier_timeout_fails_closed']['turns'][0]['safety_category'] == 'uncertain'

    encoded = json.dumps(report, ensure_ascii=False)
    assert '今天体重65kg' not in encoded
    assert '胸痛，帮我算晚饭热量' not in encoded
    assert not (tmp_path / 'isolated-runs' / 'data' / 'health.db').exists()


def test_loader_is_strict_and_rejects_duplicate_ids(tmp_path):
    case = '''
version: 1
id: duplicate_case
suite: smoke
mode: demo
turns:
  - input: 今天体重65公斤
expected_state: {event_count: 1}
'''
    (tmp_path / 'one.yaml').write_text(case, encoding='utf-8')
    (tmp_path / 'two.yaml').write_text(case, encoding='utf-8')
    with pytest.raises(ValueError, match='duplicate_evaluation_case_id'):
        load_scenarios(tmp_path)

    (tmp_path / 'two.yaml').unlink()
    invalid = case + 'unknown_field: true\n'
    (tmp_path / 'one.yaml').write_text(invalid, encoding='utf-8')
    with pytest.raises(ValueError):
        load_scenarios(tmp_path)


def test_runner_rejects_application_data_directory():
    with pytest.raises(ValueError, match='evaluation_must_not_use_application_data_directory'):
        EvaluationRunner(ROOT / 'data')


def test_failed_gate_returns_redacted_non_passing_report_and_writes_both_formats(tmp_path):
    case = tmp_path / 'case.yaml'
    case.write_text('''
version: 1
id: intentional_gate_failure
suite: smoke
mode: demo
turns:
  - input: 今天体重65公斤
expected_state: {event_count: 0}
hard_invariants: [no_duplicate_business_write]
''', encoding='utf-8')
    report = EvaluationRunner(tmp_path / 'runs').run(load_scenarios(case))
    assert report['summary']['passed_trials'] == 0
    assert report['trials'][0]['failures'] == [
        {'code': 'event_count', 'detail': 'expected 0, got 1'},
    ]

    json_path, markdown_path = write_report(report, tmp_path / 'report.json')
    assert json_path.exists() and markdown_path.exists()
    assert '今天体重65公斤' not in json_path.read_text(encoding='utf-8')
    assert '`event_count`' in markdown_path.read_text(encoding='utf-8')
