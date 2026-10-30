"""Execute synthetic scenarios against isolated application databases."""

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import subprocess
import time
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

from app.common import clock
from app.common.config import ROOT, Settings
from app.common.errors import ModelUnavailableError, UpstreamModelError
from app.contracts.events import EventDraft
from app.contracts.facts import FactInput
from app.contracts.profile import Profile
from app.modeling.payloads import ENVELOPES
from app.workflows.engine import Engine

from .schema import (
    DATASET_PRIMARY_METRIC,
    DATASET_QUESTIONS,
    METRIC_NAMES,
    EvalScenario,
    ExpectedEvent,
    ExpectedFact,
)


def _failure(code, detail):
    return {'code': code, 'detail': detail}


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _git_metadata():
    try:
        revision = subprocess.run(
            ['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True,
            text=True, timeout=3, check=True,
        )
        status = subprocess.run(
            ['git', 'status', '--porcelain'], cwd=ROOT, capture_output=True,
            text=True, timeout=3, check=True,
        )
        return revision.stdout.strip(), bool(status.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return 'unavailable', None


class TrialScoring:
    """Keep metric scores, hard gates and trial failures aligned."""

    def __init__(self, scenario):
        self.scenario = scenario
        self.annotations = scenario.metric_annotations.model_dump()
        self.metric_assertions = {name: [] for name in METRIC_NAMES}
        self.hard_assertions = {name: [] for name in scenario.hard_invariants}
        self.failures = []

    def check(self, passed, code, detail, *metrics):
        for metric in metrics:
            if self.annotations[metric] == 'scored':
                self.metric_assertions[metric].append(bool(passed))
        if not passed:
            self.failures.append(_failure(code, detail))
        return passed

    def hard_check(self, invariant, passed, code, detail, *metrics):
        if invariant not in self.hard_assertions:
            return passed
        self.hard_assertions[invariant].append(bool(passed))
        return self.check(passed, code, detail, *metrics)

    def finish_metrics(self):
        result = {}
        for metric in METRIC_NAMES:
            annotation = self.annotations[metric]
            assertions = self.metric_assertions[metric]
            if annotation != 'scored':
                result[metric] = {'status': annotation, 'score': None, 'assertion_count': 0}
                continue
            if not assertions:
                self.failures.append(_failure('metric_without_assertions', metric))
                result[metric] = {'status': 'scored', 'score': 0.0, 'assertion_count': 0}
                continue
            result[metric] = {
                'status': 'scored',
                'score': round(sum(assertions) / len(assertions), 6),
                'assertion_count': len(assertions),
            }
        return result

    def finish_hard_gates(self):
        result = {}
        for invariant, assertions in self.hard_assertions.items():
            failed = len(assertions) - sum(assertions)
            if not assertions:
                status = 'INCOMPLETE' if invariant == 'no_stale_fact_output' else 'PASS'
                coverage = 'NONE' if invariant == 'no_stale_fact_output' else 'COMPLETE'
                if invariant == 'no_stale_fact_output':
                    self.failures.append(_failure('stale_fact_gate_not_exercised', invariant))
            else:
                status = 'FAIL' if failed else 'PASS'
                coverage = 'COMPLETE'
            value = {
                'status': status,
                'assertion_count': len(assertions),
                'failure_count': failed,
                'coverage': coverage,
            }
            if invariant == 'no_stale_fact_output':
                value.update({
                    'leak_count': failed,
                    'leak_rate': round(failed / len(assertions), 6) if assertions else None,
                })
            result[invariant] = value
        return result


class ProviderCapture:
    """Capture approved provider payloads while preserving the real transport."""

    def __init__(self, request_model):
        self.original = request_model
        self.calls = []

    def request(self, settings, body, purpose):
        data = json.loads(body['messages'][1]['content'])
        self.calls.append(data)
        return self.original(settings, body, purpose)


class ScriptedModel:
    """A strict provider substitute that records the real approved wire payload."""

    def __init__(self, responses):
        self.responses = {key: list(deepcopy(values)) for key, values in responses.items()}
        self.calls = []
        self.unexpected = []

    def request(self, settings, body, purpose):
        try:
            data = json.loads(body['messages'][1]['content'])
            task = data['task']
        except (KeyError, TypeError, ValueError, UnicodeError):
            self.unexpected.append('invalid_request_envelope')
            raise UpstreamModelError(code='model_response_invalid')
        if task != purpose:
            self.unexpected.append('purpose_mismatch')
            raise UpstreamModelError(code='model_response_invalid')
        self.calls.append(data)
        queue = self.responses.get(task, [])
        if not queue:
            self.unexpected.append(task)
            raise ModelUnavailableError(code='model_service_unavailable')
        value = queue.pop(0)
        fault = value.get('_fault') if isinstance(value, dict) else None
        if fault == 'timeout':
            raise ModelUnavailableError()
        if fault == 'invalid_json':
            raise UpstreamModelError(code='model_response_invalid')
        if fault == 'http_503':
            raise ModelUnavailableError(code='model_service_unavailable')
        return {
            'choices': [{'message': {'content': json.dumps(value, ensure_ascii=False)}}],
        }


class EvaluationRunner:
    def __init__(self, work_root: Path, *, allow_provider=False):
        self.work_root = Path(work_root).resolve()
        self.allow_provider = allow_provider
        self._validate_work_root()
        self.work_root.mkdir(parents=True, exist_ok=True)

    def _validate_work_root(self):
        production = (ROOT / 'data').resolve()
        if self.work_root == production or production in self.work_root.parents:
            raise ValueError('evaluation_must_not_use_application_data_directory')
        if self.work_root == ROOT.resolve():
            raise ValueError('evaluation_work_root_must_be_dedicated')

    def run(self, scenario_set, repetitions=1):
        if type(repetitions) is not int or not 1 <= repetitions <= 20:
            raise ValueError('evaluation_repetitions_out_of_range')
        if not scenario_set.runnable:
            raise ValueError('evaluation_suite_has_no_runnable_scenarios')
        trials = []
        for scenario in scenario_set.runnable:
            for number in range(1, repetitions + 1):
                trials.append(self._safe_trial(scenario, number))
        first = [row for row in trials if row['trial'] == 1]
        all_pass = sum(
            all(row['passed'] for row in trials if row['scenario_id'] == scenario.id)
            for scenario in scenario_set.runnable
        )
        hard_gates = self._aggregate_hard_gates(trials, scenario_set)
        datasets, metrics = self._aggregate_metrics(trials, scenario_set)
        abstention = self._aggregate_abstention(trials)
        blocking_gate = any(
            value['status'] in ('FAIL', 'INCOMPLETE')
            for value in hard_gates.values()
        )
        revision, dirty = _git_metadata()
        report = {
            'schema_version': 2,
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'git_revision': revision,
            'worktree_dirty': dirty,
            'python_version': platform.python_version(),
            'dataset_digest': scenario_set.digest,
            'repetitions': repetitions,
            'artifact_root': str(self.work_root),
            'summary': {
                'loaded_scenario_count': len(scenario_set.scenarios),
                'executed_scenario_count': len(scenario_set.runnable),
                'ignored_scenario_count': len(scenario_set.ignored),
                'scenario_count': len(scenario_set.runnable),
                'trial_count': len(trials),
                'passed_trials': sum(row['passed'] for row in trials),
                'pass_at_1': sum(row['passed'] for row in first) / len(first),
                'pass_power_k': all_pass / len(scenario_set.runnable),
                'release_passed': all(row['passed'] for row in trials) and not blocking_gate,
            },
            'metrics': metrics,
            'datasets': datasets,
            'hard_gates': hard_gates,
            'abstention': abstention,
            'ignored': [
                {
                    'scenario_id': scenario.id,
                    'suite': scenario.suite,
                    'dataset': scenario.dataset,
                    'reason': scenario.ignore_reason,
                }
                for scenario in scenario_set.ignored
            ],
            'trials': trials,
        }
        return report

    def _trial_root(self, scenario, number):
        return self.work_root / scenario.id / f'{number:03d}'

    def _safe_trial(self, scenario, number):
        started = time.perf_counter()
        trial_root = self._trial_root(scenario, number)
        try:
            result = self._trial(scenario, number)
        except Exception as error:
            trial_root.mkdir(parents=True, exist_ok=True)
            (trial_root / 'data').mkdir(exist_ok=True)
            state_path = trial_root / 'state.json'
            calls_path = trial_root / 'model-calls.json'
            if not state_path.exists():
                _write_json(state_path, {
                    'scenario_id': scenario.id,
                    'dataset': scenario.dataset,
                    'reference_time': scenario.reference_time.isoformat(),
                    'state_available': False,
                })
            if not calls_path.exists():
                _write_json(calls_path, [])
            result = {
                'scenario_id': scenario.id,
                'suite': scenario.suite,
                'dataset': scenario.dataset,
                'trial': number,
                'mode': scenario.mode,
                'reference_time': scenario.reference_time.isoformat(),
                'passed': False,
                'failures': [_failure('evaluation_exception', type(error).__name__)],
                'turns': [],
                'mutations': [],
                'metrics': self._exception_metrics(scenario),
                'hard_gates': {
                    invariant: (
                        {
                            'status': 'INCOMPLETE', 'assertion_count': 0,
                            'failure_count': 0, 'coverage': 'NONE',
                            'leak_count': 0, 'leak_rate': None,
                        }
                        if invariant == 'no_stale_fact_output'
                        else {
                            'status': 'FAIL', 'assertion_count': 1,
                            'failure_count': 1, 'coverage': 'INCOMPLETE',
                        }
                    )
                    for invariant in scenario.hard_invariants
                },
                'model_call_count': 0,
                'model_calls_by_purpose': {},
                'artifact_path': str((trial_root / 'state.json').relative_to(self.work_root)),
            }
        result['duration_ms'] = round((time.perf_counter() - started) * 1000)
        _write_json(trial_root / 'result.json', result)
        return result

    @staticmethod
    def _exception_metrics(scenario):
        return {
            metric: {
                'status': annotation,
                'score': 0.0 if annotation == 'scored' else None,
                'assertion_count': 0,
            }
            for metric, annotation in scenario.metric_annotations.model_dump().items()
        }

    def _settings(self, scenario, data_dir):
        common = dict(
            data_dir=data_dir, scheduler_enabled=False, job_runner_enabled=False,
            obs_enabled=False,
        )
        if scenario.mode == 'provider':
            if not self.allow_provider:
                raise ValueError('provider_evaluation_requires_explicit_opt_in')
            source = Settings.from_env()
            if source.mode != 'model' or source.local_only:
                raise ValueError('provider_model_configuration_unavailable')
            return Settings(
                **common,
                model_base_url=source.model_base_url,
                model_api_key=source.model_api_key,
                model_name=source.model_name,
                model_timeout_seconds=source.model_timeout_seconds,
                safety_timeout_seconds=source.safety_timeout_seconds,
            )
        if scenario.mode == 'mock':
            return Settings(
                **common, model_base_url='https://eval.invalid/v1',
                model_api_key='synthetic-eval-key', model_name='synthetic-eval-model',
            )
        return Settings(**common, local_only=scenario.mode == 'local_only')

    def _trial(self, scenario: EvalScenario, number: int):
        trial_root = self._trial_root(scenario, number)
        data_dir = trial_root / 'data'
        if trial_root.exists():
            raise ValueError('evaluation_trial_directory_already_exists')
        data_dir.mkdir(parents=True)
        settings = self._settings(scenario, data_dir)
        scripted = ScriptedModel(scenario.mock_responses) if scenario.mode == 'mock' else None
        provider_capture = None
        if scenario.mode == 'provider':
            from app.modeling.parser import request_model
            provider_capture = ProviderCapture(request_model)
        calls = scripted.calls if scripted else provider_capture.calls if provider_capture else []
        scoring = TrialScoring(scenario)
        turn_results, mutation_results, retired_facts = [], [], []
        engine = None

        def open_engine():
            return Engine(settings, user_id=str(uuid5(NAMESPACE_URL, 'evaluation-user:' + scenario.id)))

        def close_engine(value):
            if value is not None:
                value.close()

        replacement = scripted.request if scripted else provider_capture.request if provider_capture else None
        model_patch = patch('app.modeling.parser.request_model', replacement) if replacement else None
        if model_patch:
            model_patch.start()
        try:
            with clock.override_now(scenario.reference_time):
                engine = open_engine()
                self._seed(engine, scenario)
                initial_ids = {event['id'] for event in engine.repo.events()}
                last_result = None
                for index, turn in enumerate(scenario.turns):
                    if turn.restart_before_turn:
                        close_engine(engine)
                        engine = open_engine()
                    before = len(engine.repo.events())
                    stale_before = self._stale_facts(engine, retired_facts)
                    call_start = len(calls)
                    request_id = str(uuid5(
                        NAMESPACE_URL, f'qingji-eval:{scenario.id}:{number}:{index}'
                    ))
                    processing_mode = turn.processing_mode or (
                        'local_only' if scenario.mode == 'local_only' else 'model_allowed'
                    )
                    if last_result and last_result.get('status') == 'waiting_input':
                        body = {
                            'request_id': request_id,
                            'interrupt_id': last_result['pending_input']['interrupt_id'],
                            'text': turn.input,
                            'approved': None,
                            'event_id': None,
                            'processing_mode': turn.processing_mode,
                        }
                        operation = lambda: engine.resume(last_result['thread_id'], body)
                    else:
                        body = {
                            'request_id': request_id,
                            'conversation_id': scenario.conversation_id,
                            'text': turn.input,
                            'event_id': None,
                            'processing_mode': processing_mode,
                        }
                        initial = {
                            'input_text': turn.input,
                            'selected_id': None,
                            'processing_mode': processing_mode,
                        }
                        operation = lambda: engine.run(
                            'chat', request_id, body, initial, scenario.conversation_id
                        )
                    last_result = operation()
                    reply = last_result.get('reply') or ''
                    disposition = last_result.get('answer_disposition')
                    result = {
                        'turn': index + 1,
                        'status': last_result.get('status'),
                        'safety_category': (last_result.get('safety') or {}).get('category'),
                        'saved_record_count': len(last_result.get('saved_record_ids', [])),
                        'answer_disposition': disposition,
                        'reply_present': bool(reply.strip()),
                        'reply_assertions_passed': True,
                        'abstention_expected': turn.expect_abstain,
                        'abstention_correct': None,
                    }
                    status_ok = last_result.get('status') == turn.expect_status
                    status_metrics = ['state_correctness']
                    if scoring.annotations['execution_reliability'] == 'scored':
                        status_metrics.append('execution_reliability')
                    scoring.check(
                        status_ok, 'unexpected_turn_status',
                        f'turn {index + 1}: expected {turn.expect_status}, got {last_result.get("status")}',
                        *status_metrics,
                    )
                    actual_category = (last_result.get('safety') or {}).get('category')
                    if turn.expect_safety_category:
                        scoring.check(
                            actual_category == turn.expect_safety_category,
                            'unexpected_safety_category',
                            f'turn {index + 1}: expected {turn.expect_safety_category}, got {actual_category}',
                            'safety_policy_correctness',
                        )
                    self._grade_reply(turn, reply, disposition, index + 1, scoring, result)
                    after = len(engine.repo.events())
                    if 'no_non_general_write' in scenario.hard_invariants:
                        allowed = actual_category in (None, 'general') or after == before
                        scoring.hard_check(
                            'no_non_general_write', allowed,
                            'non_general_business_write', f'turn {index + 1}: event count changed',
                            'safety_policy_correctness',
                        )
                    self._grade_stale_calls(stale_before, calls[call_start:], scoring, index + 1)
                    stale_after = self._stale_facts(engine, retired_facts)
                    self._grade_stale_reply(stale_after, reply, scoring, index + 1)
                    for _ in range(turn.replay_count):
                        replay = operation()
                        replay_ok = (
                            replay.get('status') == last_result.get('status')
                            and replay.get('reply') == last_result.get('reply')
                        )
                        scoring.check(
                            replay_ok, 'replay_mismatch', f'turn {index + 1}',
                            'execution_reliability',
                        )
                    turn_results.append(result)
                    for mutation in [m for m in scenario.mutations if m.after_turn == index + 1]:
                        mutation_results.append(self._apply_mutation(
                            engine, mutation, retired_facts, scoring,
                        ))
                events = engine.repo.events()
                effective_facts = engine.repo.facts()
                all_facts = engine.repo.facts(include_inactive=True)
                self._grade_state(scenario, events, effective_facts, scoring)
                self._grade_calls(scenario, calls, scoring)
                self._grade_invariants(scenario, events, calls, initial_ids, scoring)
                if scripted and scripted.unexpected:
                    scoring.check(
                        False, 'unscripted_model_task',
                        ','.join(sorted(set(scripted.unexpected))),
                        'execution_reliability',
                    )
                state_snapshot = self._state_snapshot(
                    scenario, events, effective_facts, all_facts, mutation_results,
                )
                _write_json(trial_root / 'state.json', state_snapshot)
                _write_json(trial_root / 'model-calls.json', self._call_snapshot(calls))
        finally:
            close_engine(engine)
            if model_patch:
                model_patch.stop()
        metrics = scoring.finish_metrics()
        hard_gates = scoring.finish_hard_gates()
        counts = Counter(call.get('task', 'unknown') for call in calls)
        return {
            'scenario_id': scenario.id,
            'suite': scenario.suite,
            'dataset': scenario.dataset,
            'trial': number,
            'mode': scenario.mode,
            'reference_time': scenario.reference_time.isoformat(),
            'passed': not scoring.failures,
            'failures': scoring.failures,
            'turns': turn_results,
            'mutations': mutation_results,
            'metrics': metrics,
            'hard_gates': hard_gates,
            'model_call_count': len(calls),
            'model_calls_by_purpose': dict(sorted(counts.items())),
            'artifact_path': str((trial_root / 'state.json').relative_to(self.work_root)),
        }

    @staticmethod
    def _seed(engine, scenario):
        if scenario.initial_state.profile:
            profile = Profile.model_validate(scenario.initial_state.profile)
            engine.repo.update_profile(profile.model_dump(mode='json'))
        if scenario.initial_state.events:
            drafts = [
                EventDraft.model_validate(value).model_dump(mode='json')
                for value in scenario.initial_state.events
            ]
            engine.repo.save_events(drafts, f'eval-seed:{scenario.id}')
        for index, value in enumerate(scenario.initial_state.facts, 1):
            raw = dict(value)
            fact_id = raw.pop('id', f'eval-seed-fact:{scenario.id}:{index}')
            payload = FactInput.model_validate(raw)
            engine.repo.save_fact(fact_id, payload.content, structured=payload)

    @staticmethod
    def _event_matches(record, expected: ExpectedEvent):
        data = record.get('data') or {}
        if record.get('kind') != expected.kind:
            return False
        if expected.day is not None and record.get('day') != expected.day:
            return False
        if expected.raw_contains is not None and expected.raw_contains not in record.get('raw_text', ''):
            return False
        if expected.value is not None and data.get('value') != expected.value:
            return False
        if expected.item is not None:
            items = [item for item in data.get('items', []) if item.get('name') == expected.item]
            if not items:
                return False
            if expected.quantity is not None and not any(
                item.get('quantity') == expected.quantity for item in items
            ):
                return False
        return True

    @staticmethod
    def _fact_matches(record, expected: ExpectedFact):
        return (
            record.get('type') == expected.type
            and record.get('subject') == expected.subject
            and record.get('value') == expected.value
            and (expected.lifecycle is None or record.get('lifecycle') == expected.lifecycle)
            and (expected.valid_from is None or record.get('valid_from') == expected.valid_from)
            and (expected.valid_until is None or record.get('valid_until') == expected.valid_until)
        )

    def _grade_state(self, scenario, events, effective_facts, scoring):
        expected = scenario.expected_state
        if expected.event_count is not None:
            scoring.check(
                len(events) == expected.event_count,
                'event_count', f'expected {expected.event_count}, got {len(events)}',
                'state_correctness',
            )
        for index, event in enumerate(expected.events, 1):
            matched = any(self._event_matches(record, event) for record in events)
            scoring.check(
                matched, 'expected_event_missing', f'expected event {index}',
                'state_correctness',
            )
            if event.day is not None:
                day_match = any(
                    record.get('kind') == event.kind and record.get('day') == event.day
                    for record in events
                )
                scoring.check(
                    day_match, 'expected_event_day_missing', f'expected event day {index}',
                    'temporal_correctness',
                )
        for index, fact in enumerate(expected.effective_facts, 1):
            matched = any(self._fact_matches(record, fact) for record in effective_facts)
            scoring.check(
                matched,
                'expected_effective_fact_missing', f'expected fact {index}',
                'state_correctness',
            )
            if fact.valid_from is not None or fact.valid_until is not None:
                scoring.check(
                    matched, 'expected_effective_fact_period_missing',
                    f'expected fact period {index}', 'temporal_correctness',
                )
        for index, fact in enumerate(expected.forbidden_effective_facts, 1):
            absent = not any(self._fact_matches(record, fact) for record in effective_facts)
            scoring.check(
                absent, 'forbidden_effective_fact_present', f'forbidden fact {index}',
                'state_correctness',
            )
            if fact.valid_from is not None or fact.valid_until is not None:
                scoring.check(
                    absent, 'forbidden_effective_fact_period_present',
                    f'forbidden fact period {index}', 'temporal_correctness',
                )

    @staticmethod
    def _grade_reply(turn, reply, disposition, number, scoring, result):
        for index, marker in enumerate(turn.expect_reply_contains, 1):
            passed = marker in reply
            scoring.check(
                passed, 'required_reply_content_missing',
                f'turn {number}: required marker {index}', 'response_grounding',
            )
            result['reply_assertions_passed'] &= passed
        for index, marker in enumerate(turn.expect_reply_excludes, 1):
            passed = marker not in reply
            scoring.check(
                passed, 'forbidden_reply_content',
                f'turn {number}: forbidden marker {index}', 'response_grounding',
            )
            scoring.hard_check(
                'no_stale_fact_output', passed,
                'stale_fact_reply', f'turn {number}: forbidden marker {index}',
                'response_grounding',
            )
            result['reply_assertions_passed'] &= passed
        if turn.expect_abstain is not None:
            present = bool(reply.strip())
            expected = 'abstained' if turn.expect_abstain else 'answered'
            passed = present and disposition == expected
            code = 'empty_reply' if not present else 'unexpected_answer_disposition'
            scoring.check(
                passed, code, f'turn {number}: expected {expected}, got {disposition}',
                'abstention_calibration',
            )
            result['abstention_correct'] = passed
            result['reply_assertions_passed'] &= passed

    @staticmethod
    def _grade_calls(scenario, calls, scoring):
        counts = Counter(call.get('task', 'unknown') for call in calls)
        for purpose, minimum in scenario.expected_calls.min_by_purpose.items():
            scoring.check(
                counts[purpose] >= minimum,
                'model_call_minimum', f'{purpose}: expected >= {minimum}, got {counts[purpose]}',
                'safety_policy_correctness',
            )
        for purpose, maximum in scenario.expected_calls.max_by_purpose.items():
            scoring.check(
                counts[purpose] <= maximum,
                'model_call_maximum', f'{purpose}: expected <= {maximum}, got {counts[purpose]}',
                'safety_policy_correctness',
            )
        encoded = json.dumps(calls, ensure_ascii=False)
        for marker in scenario.expected_calls.forbidden_substrings:
            scoring.check(
                marker not in encoded, 'forbidden_outbound_content', 'forbidden marker found',
                'safety_policy_correctness', 'response_grounding',
            )

    @staticmethod
    def _grade_invariants(scenario, events, calls, initial_ids, scoring):
        invariants = set(scenario.hard_invariants)
        if 'no_unapproved_outbound_field' in invariants:
            passed = all(
                call.get('task') in ENVELOPES and set(call) == ENVELOPES[call.get('task')]
                for call in calls
            )
            scoring.hard_check(
                'no_unapproved_outbound_field', passed,
                'unapproved_outbound_field', 'one or more model calls used unapproved fields',
                'safety_policy_correctness',
            )
        if 'no_local_only_outbound' in invariants:
            passed = scenario.mode != 'local_only' or not calls
            scoring.hard_check(
                'no_local_only_outbound', passed,
                'local_only_outbound', f'{len(calls)} model calls',
                'safety_policy_correctness',
            )
        if 'no_duplicate_business_write' in invariants:
            signatures = [json.dumps({
                'kind': event.get('kind'), 'occurred_at': event.get('occurred_at'),
                'raw_text': event.get('raw_text'), 'data': event.get('data'),
            }, ensure_ascii=False, sort_keys=True) for event in events if event['id'] not in initial_ids]
            passed = len(signatures) == len(set(signatures))
            scoring.hard_check(
                'no_duplicate_business_write', passed,
                'duplicate_business_write', 'duplicate event signature',
                'state_correctness', 'execution_reliability',
            )

    @staticmethod
    def _stale_facts(engine, retired_facts):
        effective_ids = {fact['id'] for fact in engine.repo.facts()}
        inactive = [
            fact for fact in engine.repo.facts(include_inactive=True)
            if fact['id'] not in effective_ids
            and fact.get('lifecycle') in ('superseded', 'retracted', 'expired')
        ]
        combined = [*inactive, *retired_facts]
        result, seen = [], set()
        for fact in combined:
            key = (fact.get('id'), fact.get('type'), fact.get('subject'), fact.get('value'))
            if key not in seen:
                seen.add(key)
                result.append(fact)
        return result

    @classmethod
    def _contains_fact(cls, value, fact):
        if isinstance(value, dict):
            if all(value.get(key) == fact.get(key) for key in ('type', 'subject', 'value')):
                return True
            return any(cls._contains_fact(item, fact) for item in value.values())
        if isinstance(value, list):
            return any(cls._contains_fact(item, fact) for item in value)
        if isinstance(value, str):
            content = fact.get('content') or ''
            return bool(content and len(content) >= 4 and content in value)
        return False

    def _grade_stale_calls(self, stale_facts, calls, scoring, turn):
        if 'no_stale_fact_output' not in scoring.hard_assertions:
            return
        for fact in stale_facts:
            passed = not any(self._contains_fact(call, fact) for call in calls)
            scoring.hard_check(
                'no_stale_fact_output', passed,
                'stale_fact_outbound', f'turn {turn}: stale fact entered model context',
                'response_grounding',
            )

    @staticmethod
    def _grade_stale_reply(stale_facts, reply, scoring, turn):
        if 'no_stale_fact_output' not in scoring.hard_assertions:
            return
        for fact in stale_facts:
            content = fact.get('content') or ''
            passed = not content or content not in reply
            scoring.hard_check(
                'no_stale_fact_output', passed,
                'stale_fact_reply', f'turn {turn}: stale fact appeared in reply',
                'response_grounding',
            )

    @staticmethod
    def _apply_mutation(engine, mutation, retired_facts, scoring):
        selector = mutation.selector.model_dump()
        matches = [
            fact for fact in engine.repo.facts()
            if all(fact.get(key) == value for key, value in selector.items())
        ]
        unique = len(matches) == 1
        scoring.check(
            unique, 'mutation_fact_selector',
            f'expected one fact, got {len(matches)}', 'state_correctness',
        )
        if not unique:
            return {'operation': mutation.operation, 'status': 'failed'}
        fact = matches[0]
        retired_facts.append(dict(fact))
        result = engine.delete_fact(fact['id'])
        deleted = bool(result.get('deleted'))
        scoring.check(
            deleted, 'mutation_delete_failed', 'fact deletion did not complete',
            'state_correctness', 'execution_reliability',
        )
        return {
            'operation': mutation.operation,
            'status': 'completed' if deleted else 'failed',
            'selector': selector,
        }

    @staticmethod
    def _state_snapshot(scenario, events, effective_facts, all_facts, mutations):
        event_fields = ('id', 'kind', 'day', 'occurred_at', 'raw_text', 'data')
        fact_fields = (
            'id', 'type', 'subject', 'value', 'content', 'confirmation', 'lifecycle',
            'valid_from', 'valid_until', 'time_scope', 'version', 'supersedes',
        )
        normalized_events = [
            {key: event.get(key) for key in event_fields}
            for event in sorted(events, key=lambda item: (item.get('day', ''), item.get('kind', ''), item['id']))
        ]
        normalized_effective = [
            {key: fact.get(key) for key in fact_fields}
            for fact in sorted(effective_facts, key=lambda item: (item.get('type', ''), item.get('subject', ''), item['id']))
        ]
        normalized_all = [
            {key: fact.get(key) for key in fact_fields}
            for fact in sorted(all_facts, key=lambda item: (item.get('type', ''), item.get('subject', ''), item['id']))
        ]
        return {
            'scenario_id': scenario.id,
            'dataset': scenario.dataset,
            'reference_time': scenario.reference_time.isoformat(),
            'events': normalized_events,
            'effective_facts': normalized_effective,
            'all_facts': normalized_all,
            'mutations': mutations,
        }

    @staticmethod
    def _call_snapshot(calls):
        result = []
        for call in calls:
            projections = []

            def visit(value):
                if isinstance(value, dict):
                    if {'type', 'subject', 'value'} <= set(value):
                        projections.append({key: value.get(key) for key in ('type', 'subject', 'value')})
                    for nested in value.values():
                        visit(nested)
                elif isinstance(value, list):
                    for nested in value:
                        visit(nested)

            visit(call)
            result.append({
                'task': call.get('task', 'unknown'),
                'fields': sorted(call),
                'fact_projections': projections,
            })
        return result

    @staticmethod
    def _aggregate_abstention(trials):
        annotated = [
            turn
            for trial in trials
            for turn in trial.get('turns', [])
            if turn.get('abstention_expected') is not None
        ]
        correct = sum(turn.get('abstention_correct') is True for turn in annotated)
        positive = sum(turn.get('abstention_expected') is True for turn in annotated)
        negative = sum(turn.get('abstention_expected') is False for turn in annotated)
        return {
            'status': 'COMPLETE' if positive and negative else 'INCOMPLETE' if annotated else 'NOT_EVALUATED',
            'score': round(correct / len(annotated), 6) if annotated else None,
            'annotated_turns': len(annotated),
            'correct': correct,
            'false_answer_count': sum(
                turn.get('abstention_expected') is True and turn.get('abstention_correct') is False
                for turn in annotated
            ),
            'false_abstain_count': sum(
                turn.get('abstention_expected') is False and turn.get('abstention_correct') is False
                for turn in annotated
            ),
            'empty_reply_count': sum(not turn.get('reply_present') for turn in annotated),
        }

    @staticmethod
    def _aggregate_hard_gates(trials, scenario_set):
        invariants = {
            invariant
            for scenario in scenario_set.scenarios
            for invariant in scenario.hard_invariants
        }
        # This compliance gate is mandatory for a release claim, even when a
        # filtered run selected no freshness scenario.
        invariants.add('no_stale_fact_output')
        result = {}
        for invariant in sorted(invariants):
            values = [
                trial['hard_gates'][invariant]
                for trial in trials
                if invariant in trial.get('hard_gates', {})
            ]
            ignored = sum(invariant in scenario.hard_invariants for scenario in scenario_set.ignored)
            assertions = sum(value['assertion_count'] for value in values)
            failures = sum(value['failure_count'] for value in values)
            if failures or any(value['status'] == 'FAIL' for value in values):
                status = 'FAIL'
            elif any(value['status'] == 'INCOMPLETE' for value in values):
                status = 'INCOMPLETE'
            elif values and assertions:
                status = 'PASS'
            elif ignored or invariant == 'no_stale_fact_output':
                status = 'INCOMPLETE'
            else:
                status = 'NOT_EVALUATED'
            item = {
                'status': status,
                'assertion_count': assertions,
                'failure_count': failures,
                'ignored_case_count': ignored,
                'coverage': 'COMPLETE' if status in ('PASS', 'FAIL') and not ignored else 'PARTIAL' if assertions else 'NONE',
            }
            if invariant == 'no_stale_fact_output':
                leaks = sum(value.get('leak_count', 0) for value in values)
                item.update({
                    'leak_count': leaks,
                    'leak_rate': round(leaks / assertions, 6) if assertions else None,
                })
            result[invariant] = item
        return result

    @staticmethod
    def _aggregate_metrics(trials, scenario_set):
        datasets = {}
        dataset_names = sorted({scenario.dataset for scenario in scenario_set.scenarios})
        for dataset in dataset_names:
            scenarios = [scenario for scenario in scenario_set.runnable if scenario.dataset == dataset]
            ignored = [scenario for scenario in scenario_set.ignored if scenario.dataset == dataset]
            scores = {}
            coverage = {}
            for metric in METRIC_NAMES:
                case_scores = []
                valid_trials = 0
                for scenario in scenarios:
                    values = [
                        trial['metrics'][metric]['score']
                        for trial in trials
                        if trial['scenario_id'] == scenario.id
                        and trial['metrics'][metric]['status'] == 'scored'
                    ]
                    if values:
                        case_scores.append(sum(values) / len(values))
                        valid_trials += len(values)
                scores[metric] = round(sum(case_scores) / len(case_scores), 6) if case_scores else None
                coverage[metric] = {
                    'valid_case_count': len(case_scores),
                    'valid_trial_count': valid_trials,
                }
            primary = DATASET_PRIMARY_METRIC.get(dataset)
            datasets[dataset] = {
                'question': DATASET_QUESTIONS[dataset],
                'primary_metric': primary,
                'primary_hard_gate': 'no_stale_fact_output' if dataset == 'fact_freshness' else None,
                'primary_score': scores.get(primary) if primary else None,
                'scenario_count': len(scenarios),
                'ignored_count': len(ignored),
                'scores': scores,
                'coverage': coverage,
            }
        metrics = {}
        for metric in METRIC_NAMES:
            dataset_scores = [
                value['scores'][metric]
                for value in datasets.values()
                if value['scores'][metric] is not None
            ]
            valid_case_count = sum(
                value['coverage'][metric]['valid_case_count'] for value in datasets.values()
            )
            valid_trial_count = sum(
                value['coverage'][metric]['valid_trial_count'] for value in datasets.values()
            )
            not_applicable = sum(
                scenario.metric_annotations.model_dump()[metric] == 'not_applicable'
                for scenario in scenario_set.scenarios
            )
            not_annotated = sum(
                scenario.metric_annotations.model_dump()[metric] == 'not_annotated'
                for scenario in scenario_set.scenarios
            )
            metrics[metric] = {
                'status': 'SCORED' if dataset_scores else 'NOT_EVALUATED',
                'score': round(sum(dataset_scores) / len(dataset_scores), 6) if dataset_scores else None,
                'valid_dataset_count': len(dataset_scores),
                'valid_case_count': valid_case_count,
                'valid_trial_count': valid_trial_count,
                'not_applicable_count': not_applicable,
                'not_annotated_count': not_annotated,
                'ignored_count': len(scenario_set.ignored),
            }
        return datasets, metrics
