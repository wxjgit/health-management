"""The evaluation harness is isolated, versioned and safe to use in CI."""

import json
from pathlib import Path
import sys

import pytest

from app.common.config import ROOT
from evals import EvaluationRunner, load_scenarios
from evals.reports import write_report
from evals.schema import METRIC_NAMES, EvalScenario
from evals.runner import TrialScoring
from scripts.evaluate_agent import main as evaluate_main


CASES = Path(__file__).resolve().parents[1] / 'evals' / 'cases'
ANNOTATIONS = '''
  state_correctness: scored
  response_grounding: not_applicable
  abstention_calibration: not_applicable
  temporal_correctness: not_applicable
  safety_policy_correctness: not_applicable
  execution_reliability: not_applicable
'''


def test_bundled_scenarios_cover_metrics_gates_abstention_time_and_archives(tmp_path):
    scenario_set = load_scenarios(CASES)
    root = tmp_path / 'isolated-runs'
    report = EvaluationRunner(root).run(scenario_set)

    assert report['summary'] == {
        'loaded_scenario_count': 14,
        'executed_scenario_count': 14,
        'ignored_scenario_count': 0,
        'scenario_count': 14,
        'trial_count': 14,
        'passed_trials': 14,
        'pass_at_1': 1.0,
        'pass_power_k': 1.0,
        'release_passed': True,
    }
    assert set(report['metrics']) == set(METRIC_NAMES)
    assert 'overall_score' not in report and 'overall_score' not in report['summary']
    stale_gate = report['hard_gates']['no_stale_fact_output']
    assert stale_gate['status'] == 'PASS'
    assert stale_gate['assertion_count'] >= 4
    assert stale_gate['failure_count'] == stale_gate['leak_count'] == 0
    assert stale_gate['ignored_case_count'] == 0
    assert stale_gate['coverage'] == 'COMPLETE'
    assert stale_gate['leak_rate'] == 0.0
    assert report['abstention'] == {
        'status': 'COMPLETE',
        'score': 1.0,
        'annotated_turns': 2,
        'correct': 2,
        'false_answer_count': 0,
        'false_abstain_count': 0,
        'empty_reply_count': 0,
    }
    assert report['datasets']['fact_freshness']['primary_hard_gate'] == 'no_stale_fact_output'
    assert report['datasets']['temporal']['primary_score'] == 1.0

    rows = {row['scenario_id']: row for row in report['trials']}
    assert rows['mock_model_weight_boundary']['model_calls_by_purpose'] == {
        'processing_plan': 1, 'safety_classification': 1,
    }
    assert rows['mock_weight_resume_after_restart']['model_calls_by_purpose'] == {
        'patch_draft': 1, 'processing_plan': 1, 'safety_classification': 2,
    }
    assert rows['classifier_timeout_fails_closed']['turns'][0]['safety_category'] == 'uncertain'
    assert rows['query_unknown_abstains']['turns'][0]['answer_disposition'] == 'abstained'
    assert rows['query_known_answers']['turns'][0]['answer_disposition'] == 'answered'

    for row in report['trials']:
        trial = root / row['scenario_id'] / f"{row['trial']:03d}"
        assert (trial / 'data').is_dir()
        assert (trial / 'state.json').is_file()
        assert (trial / 'model-calls.json').is_file()
        assert (trial / 'result.json').is_file()
    encoded = json.dumps(report, ensure_ascii=False)
    assert '今天体重65kg' not in encoded
    assert '胸痛，帮我算晚饭热量' not in encoded
    assert not (root / 'data' / 'health.db').exists()


def test_loader_is_v2_strict_and_rejects_duplicate_ids(tmp_path):
    case = f'''
version: 2
id: duplicate_case
suite: smoke
dataset: recording_state
reference_time: "2026-10-30T09:00:00+08:00"
metric_annotations:{ANNOTATIONS}
mode: demo
turns:
  - input: 今天体重65公斤
expected_state: {{event_count: 1}}
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

    (tmp_path / 'one.yaml').write_text(case.replace('version: 2', 'version: 1'), encoding='utf-8')
    with pytest.raises(ValueError, match='evaluation_case_version_unsupported'):
        load_scenarios(tmp_path)


def test_runner_rejects_application_data_directory():
    with pytest.raises(ValueError, match='evaluation_must_not_use_application_data_directory'):
        EvaluationRunner(ROOT / 'data')


def test_failed_case_returns_metric_vector_and_writes_both_report_formats(tmp_path):
    case = tmp_path / 'case.yaml'
    case.write_text(f'''
version: 2
id: intentional_state_failure
suite: smoke
dataset: recording_state
reference_time: "2026-10-30T09:00:00+08:00"
metric_annotations:{ANNOTATIONS}
mode: demo
turns:
  - input: 今天体重65公斤
expected_state: {{event_count: 0}}
''', encoding='utf-8')
    report = EvaluationRunner(tmp_path / 'runs').run(load_scenarios(case))
    assert report['summary']['passed_trials'] == 0
    assert report['summary']['release_passed'] is False
    assert report['trials'][0]['failures'] == [
        {'code': 'event_count', 'detail': 'expected 0, got 1'},
    ]
    assert set(report['trials'][0]['metrics']) == set(METRIC_NAMES)
    assert report['trials'][0]['metrics']['state_correctness']['score'] == 0.5

    json_path, markdown_path = write_report(report, tmp_path / 'report.json')
    assert json_path.exists() and markdown_path.exists()
    assert '今天体重65公斤' not in json_path.read_text(encoding='utf-8')
    markdown = markdown_path.read_text(encoding='utf-8')
    assert '## Engineering metrics' in markdown
    assert '## Dataset matrix' in markdown
    assert '`event_count`' in markdown


def test_ignored_cases_are_loaded_reported_and_excluded_from_scores(tmp_path):
    active = f'''
version: 2
id: active_case
suite: smoke
dataset: recording_state
reference_time: "2026-10-30T09:00:00+08:00"
metric_annotations:{ANNOTATIONS}
mode: demo
turns:
  - input: 今天体重65公斤
expected_state: {{event_count: 1}}
'''
    ignored = active.replace('id: active_case', '''id: ignored_case
ignore: true
ignore_reason: 当前运行环境不适用该题''')
    (tmp_path / 'active.yaml').write_text(active, encoding='utf-8')
    (tmp_path / 'ignored.yaml').write_text(ignored, encoding='utf-8')

    scenarios = load_scenarios(tmp_path)
    assert len(scenarios.scenarios) == 2
    assert len(scenarios.runnable) == 1
    assert len(scenarios.ignored) == 1
    report = EvaluationRunner(tmp_path / 'runs').run(scenarios)
    assert report['summary']['ignored_scenario_count'] == 1
    assert report['metrics']['state_correctness']['score'] == 1.0
    assert report['metrics']['state_correctness']['valid_case_count'] == 1
    assert report['ignored'] == [{
        'scenario_id': 'ignored_case', 'suite': 'smoke',
        'dataset': 'recording_state', 'reason': '当前运行环境不适用该题',
    }]
    with pytest.raises(ValueError, match='evaluation_suite_has_no_runnable_scenarios'):
        EvaluationRunner(tmp_path / 'all-ignored-runs').run(
            load_scenarios(tmp_path / 'ignored.yaml')
        )


def test_metric_fusion_macro_averages_datasets_not_cases(tmp_path):
    cases = f'''
- version: 2
  id: recording_pass
  suite: capability
  dataset: recording_state
  reference_time: "2026-10-30T09:00:00+08:00"
  metric_annotations:{ANNOTATIONS.replace(chr(10), chr(10) + '  ').rstrip()}
  mode: demo
  turns: [{{input: 今天体重65公斤}}]
  expected_state: {{event_count: 1}}
- version: 2
  id: recording_partial
  suite: capability
  dataset: recording_state
  reference_time: "2026-10-30T09:00:00+08:00"
  metric_annotations:{ANNOTATIONS.replace(chr(10), chr(10) + '  ').rstrip()}
  mode: demo
  turns: [{{input: 今天体重65公斤}}]
  expected_state: {{event_count: 0}}
- version: 2
  id: temporal_partial
  suite: capability
  dataset: temporal
  reference_time: "2026-10-30T09:00:00+08:00"
  metric_annotations:
    state_correctness: scored
    response_grounding: not_applicable
    abstention_calibration: not_applicable
    temporal_correctness: scored
    safety_policy_correctness: not_applicable
    execution_reliability: not_applicable
  mode: demo
  turns: [{{input: 昨天体重65公斤}}]
  expected_state:
    event_count: 0
    events: [{{kind: weight, day: "2026-10-29", value: 65}}]
'''
    (tmp_path / 'cases.yaml').write_text(cases, encoding='utf-8')
    report = EvaluationRunner(tmp_path / 'runs').run(load_scenarios(tmp_path / 'cases.yaml'))
    dataset_scores = [
        value['scores']['state_correctness']
        for value in report['datasets'].values()
        if value['scores']['state_correctness'] is not None
    ]
    expected = round(sum(dataset_scores) / len(dataset_scores), 6)
    assert report['metrics']['state_correctness']['score'] == expected
    trial_scores = [row['metrics']['state_correctness']['score'] for row in report['trials']]
    assert expected != round(sum(trial_scores) / len(trial_scores), 6)


def test_stale_fact_gate_without_output_coverage_is_blocking(tmp_path):
    case = tmp_path / 'case.yaml'
    case.write_text('''
version: 2
id: stale_gate_not_exercised
suite: regression
dataset: fact_freshness
reference_time: "2026-10-30T09:00:00+08:00"
metric_annotations:
  state_correctness: scored
  response_grounding: not_annotated
  abstention_calibration: not_applicable
  temporal_correctness: not_applicable
  safety_policy_correctness: not_applicable
  execution_reliability: not_applicable
mode: demo
turns:
  - input: 我喜欢燕麦
expected_state: {event_count: 0}
hard_invariants: [no_stale_fact_output]
''', encoding='utf-8')
    report = EvaluationRunner(tmp_path / 'runs').run(load_scenarios(case))
    assert report['hard_gates']['no_stale_fact_output']['status'] == 'INCOMPLETE'
    assert report['summary']['release_passed'] is False
    assert {'code': 'stale_fact_gate_not_exercised', 'detail': 'no_stale_fact_output'} \
        in report['trials'][0]['failures']


def test_release_is_incomplete_when_selected_suite_has_no_stale_gate_coverage(tmp_path):
    case = tmp_path / 'case.yaml'
    case.write_text(f'''
version: 2
id: state_only_run
suite: smoke
dataset: recording_state
reference_time: "2026-10-30T09:00:00+08:00"
metric_annotations:{ANNOTATIONS}
mode: demo
turns:
  - input: 今天体重65公斤
expected_state: {{event_count: 1}}
''', encoding='utf-8')
    report = EvaluationRunner(tmp_path / 'runs').run(load_scenarios(case))
    assert report['summary']['passed_trials'] == 1
    assert report['hard_gates']['no_stale_fact_output']['status'] == 'INCOMPLETE'
    assert report['hard_gates']['no_stale_fact_output']['coverage'] == 'NONE'
    assert report['summary']['release_passed'] is False


def test_execution_exception_still_archives_trial_and_keeps_stale_gate_incomplete(tmp_path):
    case = tmp_path / 'case.yaml'
    case.write_text('''
version: 2
id: provider_not_opted_in
suite: regression
dataset: fact_freshness
reference_time: "2026-10-30T09:00:00+08:00"
metric_annotations:
  state_correctness: scored
  response_grounding: scored
  abstention_calibration: not_applicable
  temporal_correctness: not_applicable
  safety_policy_correctness: not_applicable
  execution_reliability: not_applicable
mode: provider
turns:
  - input: 我喜欢燕麦
    expect_reply_excludes: [我不喜欢燕麦]
expected_state: {event_count: 0}
hard_invariants: [no_stale_fact_output]
''', encoding='utf-8')
    root = tmp_path / 'runs'
    report = EvaluationRunner(root).run(load_scenarios(case))
    row = report['trials'][0]
    assert row['failures'] == [{'code': 'evaluation_exception', 'detail': 'ValueError'}]
    assert row['hard_gates']['no_stale_fact_output']['status'] == 'INCOMPLETE'
    assert report['hard_gates']['no_stale_fact_output']['status'] == 'INCOMPLETE'
    assert report['summary']['release_passed'] is False
    trial = root / 'provider_not_opted_in' / '001'
    assert (trial / 'data').is_dir()
    assert (trial / 'state.json').is_file()
    assert (trial / 'model-calls.json').is_file()
    assert (trial / 'result.json').is_file()


def test_empty_reply_is_not_counted_as_abstention():
    scenario = EvalScenario.model_validate({
        'version': 2,
        'id': 'empty_reply_contract',
        'suite': 'capability',
        'dataset': 'abstention',
        'reference_time': '2026-10-30T09:00:00+08:00',
        'metric_annotations': {
            'state_correctness': 'not_applicable',
            'response_grounding': 'not_applicable',
            'abstention_calibration': 'scored',
            'temporal_correctness': 'not_applicable',
            'safety_policy_correctness': 'not_applicable',
            'execution_reliability': 'not_applicable',
        },
        'turns': [{'input': '查体重历史', 'expect_abstain': True}],
    })
    scoring = TrialScoring(scenario)
    result = {'reply_assertions_passed': True, 'abstention_correct': None}
    EvaluationRunner._grade_reply(scenario.turns[0], '', 'abstained', 1, scoring, result)
    assert result['abstention_correct'] is False
    assert scoring.finish_metrics()['abstention_calibration']['score'] == 0.0
    assert scoring.failures == [{'code': 'empty_reply', 'detail': 'turn 1: expected abstained, got abstained'}]


def test_cli_keeps_a_versioned_artifact_root_and_trial_evidence(tmp_path, monkeypatch):
    output = tmp_path / 'requested-report.json'
    monkeypatch.setattr(sys, 'argv', [
        'evaluate_agent', '--cases', str(CASES), '--output', str(output),
    ])
    evaluate_main()

    roots = list(tmp_path.glob('requested-report-artifacts-*'))
    assert len(roots) == 1
    root = roots[0]
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['run_id'] == root.name
    assert manifest['git_revision']
    assert manifest['dataset_digest']
    assert manifest['reference_times'] == [
        '2026-10-28T09:00:00+08:00',
        '2026-10-30T09:00:00+08:00',
        '2026-10-31T09:00:00+08:00',
    ]
    assert Path(manifest['report_json']) == output
    assert output.is_file() and output.with_suffix('.md').is_file()
    assert len(list((root / 'trials').rglob('result.json'))) == 14
    assert len(list((root / 'trials').rglob('state.json'))) == 14
    assert len(list((root / 'trials').rglob('model-calls.json'))) == 14
