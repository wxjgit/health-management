"""Exercise overlapping real graphs with model I/O controlled by events."""

import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event, Lock
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.workflows import cancel
from app.common.clock import now
from app.common.config import Settings
from app.jobs.scheduler import tick
from app.main import create_app
from app.workflows.concurrency import WorkflowConflict
from app.workflows.engine import Engine


TARGETS = {'kcal': 1800, 'protein_g': 100, 'carbs_g': 215, 'fat_g': 60, 'exercise_minutes': 120}


class ControlledModel:
    def __init__(self):
        self.entered = Event()
        self.release = Event()
        self.guard = Lock()
        self.block_task = None
        self.calls = []

    def arm(self, task):
        self.block_task = task

    def handle(self, request):
        data = json.loads(json.loads(request.content)['messages'][1]['content'])
        with self.guard:
            self.calls.append(data)
            block = data['task'] == self.block_task and not self.entered.is_set()
            if block:
                self.entered.set()
        if block:
            assert self.release.wait(10), 'test did not release model I/O'
        if data['task'] == 'processing_plan':
            text = data['text']
            if '趋势' in text:
                action = {'intent': 'advice', 'text': text, 'data_view': 'weight_trend'}
            elif '改' in text:
                action = {'intent': 'correct', 'text': text, 'target': 'selected'}
            else:
                event = {'kind': 'weight', 'occurred_at': data['now'], 'raw_text': text}
                if '65' in text:
                    event['value'] = 65
                action = {'intent': 'record', 'text': text, 'events': [event]}
            content = {'actions': [action]}
        elif data['task'] == 'patch_draft':
            content = {'value': 65}
        elif data['task'] == 'correct_selected':
            content = {'event': {'kind': 'weight', 'occurred_at': data['record']['occurred_at'],
                                 'raw_text': data['text'], 'value': 66}}
        elif data['task'] == 'personalized_advice':
            content = {'answer': '记录还不足以判断稳定趋势，可以继续记录并观察。'}
        else:
            raise AssertionError('unexpected model task: ' + data['task'])
        return httpx.Response(200, json={
            'choices': [{'message': {'content': json.dumps(content, ensure_ascii=False)}}],
        })


@pytest.fixture
def model(monkeypatch):
    controlled = ControlledModel()
    original = httpx.Client
    monkeypatch.setattr('app.modeling.parser.httpx.Client', lambda *args, **kwargs: original(
        *args, **kwargs, transport=httpx.MockTransport(controlled.handle)
    ))
    return controlled


@pytest.fixture
def settings(tmp_path):
    return Settings(
        data_dir=tmp_path, model_base_url='https://test.invalid/v1',
        model_name='test-model', model_api_key='test-only-key', scheduler_enabled=False,
    )


@pytest.fixture
def engine(settings, model):
    runtime = Engine(settings)
    try:
        yield runtime
    finally:
        model.release.set()
        runtime.close()


def chat(engine, text='今天体重65公斤', *, conversation='main', local=False,
         request_id=None, selected_id=None):
    initial = {'input_text': text, 'selected_id': selected_id,
               'processing_mode': 'local_only' if local else 'model_allowed'}
    return engine.run('chat', request_id or str(uuid4()), initial, initial, conversation)


def plan(engine):
    return engine.run('plan', str(uuid4()), {}, {'plan': {'targets': TARGETS}})


def resume_payload(pending, **answer):
    return {'request_id': str(uuid4()),
            'interrupt_id': pending['pending_input']['interrupt_id'], **answer}


@pytest.mark.parametrize('entry', ['run', 'resume'])
@pytest.mark.parametrize('operation', ['other_chat', 'plan_confirmation', 'dashboard', 'scheduler'])
def test_slow_model_does_not_block_independent_operations(engine, model, entry, operation):
    proposal = plan(engine)
    if entry == 'resume':
        pending = chat(engine, '今天记录体重', conversation='slow')
        assert pending['status'] == 'waiting_input'
        model.arm('patch_draft')
        slow_operation = lambda: engine.resume(pending['thread_id'], resume_payload(pending, text='65公斤'))
    else:
        model.arm('processing_plan')
        slow_operation = lambda: chat(engine, conversation='slow')

    def independent_operation():
        if operation == 'other_chat':
            # Also proves a second model request can finish, not only local mode.
            return chat(engine, conversation='other')
        if operation == 'plan_confirmation':
            return engine.resume(proposal['thread_id'], resume_payload(proposal, approved=True))
        if operation == 'dashboard':
            from app.api.records import daily
            return daily(day=now().date(), engine=engine)
        return tick(engine)

    with ThreadPoolExecutor(max_workers=2) as pool:
        slow = pool.submit(slow_operation)
        try:
            assert model.entered.wait(3)
            pool.submit(independent_operation).result(timeout=3)
            assert not slow.done(), 'model I/O was supposed to remain paused'
        finally:
            model.release.set()
        # A fresh draft patch uses only its own input; unrelated writes and plan
        # confirmation must no longer invalidate it through a global version.
        assert slow.result(timeout=3)['status'] == 'completed'


@pytest.mark.parametrize('task', ['processing_plan', 'patch_draft', 'personalized_advice'])
def test_delete_during_model_io_does_not_restore_erased_context(engine, model, task):
    chat(engine, local=True)
    event_id = engine.repo.events()[0]['id']
    if task == 'patch_draft':
        pending = chat(engine, '今天记录体重', conversation='slow')
        operation = lambda: engine.resume(pending['thread_id'], resume_payload(pending, text='65公斤'))
    elif task == 'personalized_advice':
        operation = lambda: chat(engine, '最近体重有什么趋势？', conversation='slow')
    else:
        operation = lambda: chat(engine, conversation='slow')
    model.arm(task)
    with ThreadPoolExecutor(max_workers=2) as pool:
        slow = pool.submit(operation)
        try:
            assert model.entered.wait(3)
            pool.submit(engine.delete_event, event_id).result(timeout=3)
            assert not slow.done()
        finally:
            model.release.set()
        if task == 'personalized_advice':
            with pytest.raises(WorkflowConflict):
                slow.result(timeout=3)
        else:
            assert slow.result(timeout=3)['status'] == 'completed'
    assert engine.repo.event(event_id) is None
    if task == 'personalized_advice':
        assert engine.repo.events() == []
        assert engine.repo.messages() == []
        assert list(engine.checkpointer.list(None)) == []
    else:
        # This is a separate user request with a different source message, not
        # replay of the erased request. Its own record and progress survive.
        assert len(engine.repo.events()) == 1
        assert len(engine.repo.messages('slow')) >= 2
        assert engine.repo.conn.execute('SELECT COUNT(*) FROM workflows').fetchone()[0] == 1


def test_cancel_during_resume_wins_over_late_model_output(engine, model):
    pending = chat(engine, '今天记录体重')
    model.arm('patch_draft')
    with ThreadPoolExecutor(max_workers=2) as pool:
        slow = pool.submit(engine.resume, pending['thread_id'], resume_payload(pending, text='65公斤'))
        try:
            assert model.entered.wait(3)
            assert pool.submit(cancel, pending['thread_id'], engine).result(timeout=3) == {'cancelled': True}
        finally:
            model.release.set()
        with pytest.raises(WorkflowConflict):
            slow.result(timeout=3)
    assert engine.repo.workflow(pending['thread_id'])['status'] == 'cancelled'
    assert engine.repo.events() == []
    assert list(engine.checkpointer.list(None)) == []
    assert engine.repo.conn.execute('SELECT COUNT(*) FROM receipts').fetchone()[0] == 0


def test_api_preserves_independent_model_input_after_deletion(settings, model):
    with TestClient(create_app(settings)) as client:
        seed = client.post('/api/chat', json={
            'request_id': str(uuid4()), 'text': '今天体重65公斤', 'processing_mode': 'local_only',
        })
        assert seed.status_code == 200
        event_id = client.get('/api/events').json()[0]['id']
        model.arm('processing_plan')
        with ThreadPoolExecutor(max_workers=2) as pool:
            slow = pool.submit(client.post, '/api/chat', json={
                'request_id': str(uuid4()), 'text': '今天体重65公斤',
            })
            try:
                assert model.entered.wait(3)
                deleted = pool.submit(client.delete, f'/api/events/{event_id}').result(timeout=3)
                assert deleted.status_code == 200
            finally:
                model.release.set()
            assert slow.result(timeout=3).status_code == 200
        assert client.app.state.engine.repo.event(event_id) is None
        assert len(client.get('/api/events').json()) == 1
        assert len(client.get('/api/messages').json()) == 2


def test_concurrent_duplicate_run_only_calls_model_once(engine, model):
    request_id = str(uuid4())
    model.arm('processing_plan')
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(chat, engine, request_id=request_id)
        try:
            assert model.entered.wait(3)
            second = pool.submit(chat, engine, request_id=request_id)
            with pytest.raises(TimeoutError):
                second.result(timeout=0.1)
        finally:
            model.release.set()
        assert first.result(timeout=3) == second.result(timeout=3)
    assert len(model.calls) == 1
    assert len(engine.repo.events()) == 1


@pytest.mark.parametrize('same_request', [True, False])
def test_concurrent_resumes_only_advance_workflow_once(engine, model, same_request):
    pending = chat(engine, '今天记录体重')
    payload = resume_payload(pending, text='65公斤')
    second_payload = payload if same_request else {**payload, 'request_id': str(uuid4())}
    model.arm('patch_draft')
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(engine.resume, pending['thread_id'], payload)
        try:
            assert model.entered.wait(3)
            second = pool.submit(engine.resume, pending['thread_id'], second_payload)
            with pytest.raises(TimeoutError):
                second.result(timeout=0.1)
        finally:
            model.release.set()
        result = first.result(timeout=3)
        if same_request:
            assert second.result(timeout=3) == result
        else:
            with pytest.raises(ValueError, match='没有待回答问题'):
                second.result(timeout=3)
    assert sum(call['task'] == 'patch_draft' for call in model.calls) == 1
    assert len(engine.repo.events()) == 1


def test_same_conversation_still_preserves_message_order(engine, model):
    model.arm('processing_plan')
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(chat, engine)
        try:
            assert model.entered.wait(3)
            second = pool.submit(chat, engine, local=True)
            with pytest.raises(TimeoutError):
                second.result(timeout=0.1)
        finally:
            model.release.set()
        first.result(timeout=3)
        second.result(timeout=3)
    assert [message['role'] for message in engine.repo.messages()] == ['user', 'assistant', 'user', 'assistant']


def test_correction_rejects_model_result_based_on_changed_record(engine, model):
    chat(engine, local=True)
    record = engine.repo.events()[0]
    model.arm('correct_selected')
    with ThreadPoolExecutor(max_workers=2) as pool:
        slow = pool.submit(chat, engine, '把体重改成66公斤', selected_id=record['id'])
        try:
            assert model.entered.wait(3)
            with engine.lock:
                engine.repo.save_events(
                    [{**record['data'], 'value': 67, 'raw_text': '今天体重67公斤'}],
                    'concurrent-correction', correction_id=record['id'],
                )
        finally:
            model.release.set()
        with pytest.raises(WorkflowConflict, match='数据已变化'):
            slow.result(timeout=3)
    assert engine.repo.event(record['id'])['data']['value'] == 67


def test_weekly_scheduler_runs_graphs_without_an_outer_global_lock(engine, monkeypatch):
    engine.repo.update_profile({'age': 30})
    proposal = plan(engine)
    engine.resume(proposal['thread_id'], resume_payload(proposal, approved=True))
    moment = now()
    preferences = engine.repo.get_meta('preferences')
    preferences.update(notifications_enabled=True, summary_time='00:00', weekly_day=moment.weekday())
    engine.repo.set_meta('preferences', preferences)
    original = engine.invoke
    checked = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        def invoke(kind, *args, **kwargs):
            # A graph entered by tick must allow another request to acquire the
            # engine lock. An outer RLock acquisition would deadlock that request.
            pool.submit(chat, engine, local=True, conversation='interactive').result(timeout=3)
            checked.append(kind)
            return original(kind, *args, **kwargs)
        # The independent chat should use the actual invocation method.
        def scheduled_invoke(kind, *args, **kwargs):
            return original(kind, *args, **kwargs) if kind == 'chat' else invoke(kind, *args, **kwargs)
        monkeypatch.setattr(engine, 'invoke', scheduled_invoke)
        tick(engine, moment)
    assert checked == ['analysis', 'plan']
