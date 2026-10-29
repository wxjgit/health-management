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

from app.common.config import ROOT, Settings
from app.common.errors import ModelUnavailableError, UpstreamModelError
from app.contracts.events import EventDraft
from app.contracts.profile import Profile
from app.modeling.payloads import ENVELOPES
from app.workflows.engine import Engine

from .schema import EvalScenario, ExpectedEvent


def _failure(code, detail):
    return {'code': code, 'detail': detail}


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
        trials = []
        for scenario in scenario_set.scenarios:
            for number in range(1, repetitions + 1):
                trials.append(self._safe_trial(scenario, number))
        first = [row for row in trials if row['trial'] == 1]
        all_pass = sum(
            all(row['passed'] for row in trials if row['scenario_id'] == scenario.id)
            for scenario in scenario_set.scenarios
        )
        revision, dirty = _git_metadata()
        report = {
            'schema_version': 1,
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'git_revision': revision,
            'worktree_dirty': dirty,
            'python_version': platform.python_version(),
            'dataset_digest': scenario_set.digest,
            'repetitions': repetitions,
            'summary': {
                'scenario_count': len(scenario_set.scenarios),
                'trial_count': len(trials),
                'passed_trials': sum(row['passed'] for row in trials),
                'pass_at_1': sum(row['passed'] for row in first) / len(first),
                'pass_power_k': all_pass / len(scenario_set.scenarios),
            },
            'trials': trials,
        }
        return report

    def _safe_trial(self, scenario, number):
        started = time.perf_counter()
        try:
            result = self._trial(scenario, number)
        except Exception as error:
            result = {
                'scenario_id': scenario.id,
                'suite': scenario.suite,
                'trial': number,
                'mode': scenario.mode,
                'passed': False,
                'failures': [_failure('evaluation_exception', type(error).__name__)],
                'model_call_count': 0,
                'model_calls_by_purpose': {},
            }
        result['duration_ms'] = round((time.perf_counter() - started) * 1000)
        return result

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
        data_dir = self.work_root / f'{scenario.id}-{number}'
        if data_dir.exists():
            raise ValueError('evaluation_trial_directory_already_exists')
        data_dir.mkdir(parents=True)
        settings = self._settings(scenario, data_dir)
        scripted = ScriptedModel(scenario.mock_responses) if scenario.mode == 'mock' else None
        provider_capture = None
        if scenario.mode == 'provider':
            from app.modeling.parser import request_model
            provider_capture = ProviderCapture(request_model)
        calls = scripted.calls if scripted else provider_capture.calls if provider_capture else []
        failures, turn_results = [], []
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
            engine = open_engine()
            self._seed(engine, scenario)
            initial_ids = {event['id'] for event in engine.repo.events()}
            last_result = None
            for index, turn in enumerate(scenario.turns):
                if turn.restart_before_turn:
                    close_engine(engine)
                    engine = open_engine()
                before = len(engine.repo.events())
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
                turn_results.append({
                    'turn': index + 1,
                    'status': last_result.get('status'),
                    'safety_category': (last_result.get('safety') or {}).get('category'),
                    'saved_record_count': len(last_result.get('saved_record_ids', [])),
                })
                if last_result.get('status') != turn.expect_status:
                    failures.append(_failure(
                        'unexpected_turn_status',
                        f'turn {index + 1}: expected {turn.expect_status}, got {last_result.get("status")}',
                    ))
                actual_category = (last_result.get('safety') or {}).get('category')
                if turn.expect_safety_category and actual_category != turn.expect_safety_category:
                    failures.append(_failure(
                        'unexpected_safety_category',
                        f'turn {index + 1}: expected {turn.expect_safety_category}, got {actual_category}',
                    ))
                after = len(engine.repo.events())
                if ('no_non_general_write' in scenario.hard_invariants
                        and actual_category not in (None, 'general') and after != before):
                    failures.append(_failure(
                        'non_general_business_write', f'turn {index + 1}: event count changed',
                    ))
                for _ in range(turn.replay_count):
                    replay = operation()
                    if replay.get('status') != last_result.get('status'):
                        failures.append(_failure('replay_mismatch', f'turn {index + 1}'))
            events = engine.repo.events()
            self._grade_state(scenario, events, failures)
            self._grade_calls(scenario, calls, failures)
            self._grade_invariants(scenario, events, calls, initial_ids, failures)
            if scripted and scripted.unexpected:
                failures.append(_failure(
                    'unscripted_model_task', ','.join(sorted(set(scripted.unexpected))),
                ))
        finally:
            close_engine(engine)
            if model_patch:
                model_patch.stop()
        counts = Counter(call.get('task', 'unknown') for call in calls)
        return {
            'scenario_id': scenario.id,
            'suite': scenario.suite,
            'trial': number,
            'mode': scenario.mode,
            'passed': not failures,
            'failures': failures,
            'turns': turn_results,
            'model_call_count': len(calls),
            'model_calls_by_purpose': dict(sorted(counts.items())),
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

    def _grade_state(self, scenario, events, failures):
        expected = scenario.expected_state
        if expected.event_count is not None and len(events) != expected.event_count:
            failures.append(_failure(
                'event_count', f'expected {expected.event_count}, got {len(events)}',
            ))
        for index, event in enumerate(expected.events, 1):
            if not any(self._event_matches(record, event) for record in events):
                failures.append(_failure('expected_event_missing', f'expected event {index}'))

    @staticmethod
    def _grade_calls(scenario, calls, failures):
        counts = Counter(call.get('task', 'unknown') for call in calls)
        for purpose, minimum in scenario.expected_calls.min_by_purpose.items():
            if counts[purpose] < minimum:
                failures.append(_failure(
                    'model_call_minimum', f'{purpose}: expected >= {minimum}, got {counts[purpose]}',
                ))
        for purpose, maximum in scenario.expected_calls.max_by_purpose.items():
            if counts[purpose] > maximum:
                failures.append(_failure(
                    'model_call_maximum', f'{purpose}: expected <= {maximum}, got {counts[purpose]}',
                ))
        encoded = json.dumps(calls, ensure_ascii=False)
        for marker in scenario.expected_calls.forbidden_substrings:
            if marker in encoded:
                failures.append(_failure('forbidden_outbound_content', 'forbidden marker found'))

    @staticmethod
    def _grade_invariants(scenario, events, calls, initial_ids, failures):
        invariants = set(scenario.hard_invariants)
        if 'no_unapproved_outbound_field' in invariants:
            for call in calls:
                purpose = call.get('task')
                if purpose not in ENVELOPES or set(call) != ENVELOPES[purpose]:
                    failures.append(_failure('unapproved_outbound_field', str(purpose)))
        if 'no_local_only_outbound' in invariants and scenario.mode == 'local_only' and calls:
            failures.append(_failure('local_only_outbound', f'{len(calls)} model calls'))
        if 'no_duplicate_business_write' in invariants:
            signatures = [json.dumps({
                'kind': event.get('kind'), 'occurred_at': event.get('occurred_at'),
                'raw_text': event.get('raw_text'), 'data': event.get('data'),
            }, ensure_ascii=False, sort_keys=True) for event in events if event['id'] not in initial_ids]
            if len(signatures) != len(set(signatures)):
                failures.append(_failure('duplicate_business_write', 'duplicate event signature'))
