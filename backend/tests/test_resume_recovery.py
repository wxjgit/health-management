"""Regression cases for consumed interrupts and plan cancellation boundaries."""

from app.common.errors import ConflictError, WorkflowStateError

import sqlite3
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event
from uuid import uuid4

import pytest

from app.workflows.concurrency import WorkflowConflict
from app.workflows.engine import Engine
from test_workflow_concurrency import chat, engine, model, plan, resume_payload, settings


def conflict_on_patch(runtime, controlled):
    pending = chat(runtime, '今天记录体重', conversation='recover')
    payload = resume_payload(pending, text='65公斤')
    controlled.arm('patch_draft')
    original_patch = runtime.parser.patch

    def related_patch(*args, **kwargs):
        # Exercise consumed-interrupt recovery with a real observed dependency,
        # rather than relying on the former global data_version conflict.
        runtime.repo.events(kind='weight')
        return original_patch(*args, **kwargs)

    runtime.parser.patch = related_patch
    with ThreadPoolExecutor(max_workers=1) as pool:
        failed = pool.submit(runtime.resume, pending['thread_id'], payload)
        try:
            assert controlled.entered.wait(3)
            chat(runtime, local=True, conversation='other')
        finally:
            controlled.release.set()
            runtime.parser.patch = original_patch
        with pytest.raises(WorkflowConflict):
            failed.result(timeout=3)
    return pending, payload


@pytest.mark.parametrize('restart', [False, True])
def test_changed_answer_is_rejected_then_original_answer_can_retry(settings, model, restart):
    runtime = Engine(settings)
    try:
        pending, payload = conflict_on_patch(runtime, model)
        if restart:
            runtime.close()
            runtime = Engine(settings)
        workflow = runtime.repo.workflow(pending['thread_id'])
        assert workflow['status'] == 'resume_failed'
        assert workflow['result']['retry_request'] == payload
        assert runtime.repo.waiting_workflows()[0]['status'] == 'resume_failed'
        calls = len(model.calls)
        for changed in (
            {**payload, 'request_id': str(uuid4()), 'text': '改为67公斤'},
            {**payload, 'text': '改为67公斤'},
            {**payload, 'request_id': str(uuid4())},
        ):
            with pytest.raises(ConflictError):
                runtime.resume(pending['thread_id'], changed)
        assert len(model.calls) == calls
        assert len(runtime.repo.events()) == 1  # Only the independent update was saved.
        result = runtime.resume(pending['thread_id'], payload)
        assert result['status'] == 'completed'
        assert runtime.resume(pending['thread_id'], payload) == result
        assert len(runtime.repo.events()) == 2
        assert [call['text'] for call in model.calls if call['task'] == 'patch_draft'] == ['65公斤', '65公斤']
    finally:
        runtime.close()


def test_api_recovery_payload_round_trip_and_changed_answer_conflict(settings, model):
    from fastapi.testclient import TestClient
    from app.main import create_app
    with TestClient(create_app(settings)) as client:
        response = client.post('/api/chat', json={'request_id': str(uuid4()), 'text': '今天记录体重'})
        pending = response.json()
        path = f"/api/workflows/{pending['thread_id']}/resume"
        payload = resume_payload(pending, text='65公斤')
        model.arm('patch_draft')
        runtime = client.app.state.engine
        original_patch = runtime.parser.patch

        def related_patch(*args, **kwargs):
            runtime.repo.events(kind='weight')
            return original_patch(*args, **kwargs)

        runtime.parser.patch = related_patch
        with ThreadPoolExecutor(max_workers=1) as pool:
            failed = pool.submit(client.post, path, json=payload)
            try:
                assert model.entered.wait(3)
                other = client.post('/api/chat', json={
                    'request_id': str(uuid4()), 'text': '今天体重65公斤',
                    'conversation_id': 'other', 'processing_mode': 'local_only',
                })
                assert other.status_code == 200
            finally:
                model.release.set()
                runtime.parser.patch = original_patch
            assert failed.result(timeout=3).status_code == 409
        recovery = client.get('/api/workflows').json()[0]
        assert recovery['status'] == 'resume_failed'
        changed = client.post(path, json={**payload, 'request_id': str(uuid4()), 'text': '67公斤'})
        assert changed.status_code == 409
        assert client.post(path, json=recovery['retry_request']).status_code == 200
        assert len(client.get('/api/events').json()) == 2


def test_original_chat_request_cannot_bypass_failed_resume(engine, model):
    request_id = str(uuid4())
    pending = chat(engine, '今天记录体重', request_id=request_id)
    original = engine.invoke
    engine.invoke = lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('before graph'))
    try:
        with pytest.raises(ValueError, match='before graph'):
            engine.resume(pending['thread_id'], resume_payload(pending, text='65公斤'))
    finally:
        engine.invoke = original
    with pytest.raises(WorkflowConflict):
        chat(engine, '今天记录体重', request_id=request_id)


@pytest.mark.parametrize('restart', [False, True])
def test_failure_before_graph_consumes_interrupt_retries_original_command(settings, model, restart):
    runtime = Engine(settings)
    try:
        pending = chat(runtime, '今天记录体重')
        payload = resume_payload(pending, text='65公斤')
        original = runtime.invoke
        runtime.invoke = lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('before graph'))
        with pytest.raises(ValueError, match='before graph'):
            runtime.resume(pending['thread_id'], payload)
        runtime.invoke = original
        if restart:
            runtime.close()
            runtime = Engine(settings)
        result = runtime.resume(pending['thread_id'], payload)
        assert result['status'] == 'completed'
        assert len(runtime.repo.events()) == 1
        assert runtime.repo.events()[0]['data']['value'] == 65
    finally:
        runtime.close()


def test_startup_recovers_persisted_executing_attempt(settings, model):
    runtime = Engine(settings)
    try:
        pending = chat(runtime, '今天记录体重')
        payload = resume_payload(pending, text='65公斤')
        from app.workflows.runtime import fingerprint
        runtime.repo.begin_resume(pending['thread_id'], payload, fingerprint(payload), '65公斤', 'main')
    finally:
        runtime.close()
    runtime = Engine(settings)
    try:
        assert runtime.repo.workflow(pending['thread_id'])['status'] == 'resume_failed'
        assert runtime.resume(pending['thread_id'], payload)['status'] == 'completed'
    finally:
        runtime.close()


def test_finish_transaction_rolls_back_and_replays_without_duplicate_health_write(engine):
    pending = chat(engine, '今天记录体重')
    payload = resume_payload(pending, text='65公斤')
    engine.repo.conn.executescript("""
        CREATE TRIGGER fail_receipt BEFORE INSERT ON receipts
        BEGIN SELECT RAISE(ABORT, 'receipt failure'); END;
    """)
    with pytest.raises(sqlite3.IntegrityError, match='receipt failure'):
        engine.resume(pending['thread_id'], payload)
    assert engine.repo.workflow(pending['thread_id'])['status'] == 'resume_failed'
    assert engine.repo.resume_attempt(payload['request_id'])['status'] == 'failed'
    assert engine.repo.receipt(payload['request_id']) is None
    assert len(engine.repo.events()) == 1
    assert not any(message['id'] == payload['request_id'] + ':reply' for message in engine.repo.messages())
    engine.repo.conn.execute('DROP TRIGGER fail_receipt')
    result = engine.resume(pending['thread_id'], payload)
    assert result['status'] == 'completed'
    assert len(engine.repo.events()) == 1
    assert sum(message['id'] == payload['request_id'] + ':reply' for message in engine.repo.messages()) == 1


def test_begin_transaction_does_not_bind_answer_on_partial_failure(engine):
    pending = chat(engine, '今天记录体重')
    payload = resume_payload(pending, text='65公斤')
    engine.repo.conn.executescript("""
        CREATE TRIGGER fail_message BEFORE INSERT ON messages
        BEGIN SELECT RAISE(ABORT, 'message failure'); END;
    """)
    with pytest.raises(sqlite3.IntegrityError, match='message failure'):
        engine.resume(pending['thread_id'], payload)
    assert engine.repo.workflow(pending['thread_id'])['status'] == 'waiting_input'
    assert engine.repo.resume_attempt(payload['request_id']) is None


def test_legacy_consumed_interrupt_without_binding_rejects_new_answer(engine, monkeypatch):
    pending = chat(engine, '今天记录体重')
    payload = resume_payload(pending, text='65公斤')
    original = engine.format_result
    monkeypatch.setattr(engine, 'format_result', lambda *args: (_ for _ in ()).throw(ValueError('after graph')))
    with pytest.raises(ValueError, match='after graph'):
        engine.resume(pending['thread_id'], payload)
    monkeypatch.setattr(engine, 'format_result', original)
    engine.repo.conn.execute('DELETE FROM resume_attempts')
    engine.repo.conn.commit()
    engine.repo.workflow_result(pending['thread_id'], pending)
    with pytest.raises(WorkflowConflict):
        engine.resume(pending['thread_id'], {**payload, 'request_id': str(uuid4()), 'text': '67公斤'})
    assert len(engine.repo.events()) == 1


def test_cancel_after_activation_before_result_commit_returns_conflict(engine, monkeypatch):
    pending = plan(engine)
    payload = resume_payload(pending, approved=True)
    activated, release = Event(), Event()
    original = engine.invoke
    def pause_after_graph(*args, **kwargs):
        result = original(*args, **kwargs)
        activated.set()
        assert release.wait(10)
        return result
    monkeypatch.setattr(engine, 'invoke', pause_after_graph)
    with ThreadPoolExecutor(max_workers=2) as pool:
        approval = pool.submit(engine.resume, pending['thread_id'], payload)
        try:
            assert activated.wait(3)
            assert engine.repo.plan(pending['thread_id'])['status'] == 'active'
            with pytest.raises(WorkflowConflict, match='计划正在确认'):
                pool.submit(engine.cancel, pending['thread_id']).result(timeout=3)
            assert engine.repo.workflow(pending['thread_id'])['status'] == 'resuming'
        finally:
            release.set()
        assert approval.result(timeout=3)['status'] == 'completed'
    assert engine.repo.plan(pending['thread_id'])['status'] == 'active'
    assert engine.repo.workflow(pending['thread_id'])['status'] == 'completed'


def test_cancel_before_confirmation_prevents_activation(engine):
    pending = plan(engine)
    assert engine.cancel(pending['thread_id']) == {'cancelled': True}
    with pytest.raises(WorkflowConflict):
        engine.resume(pending['thread_id'], resume_payload(pending, approved=True))
    assert engine.repo.plan(pending['thread_id'])['status'] == 'rejected'
    assert engine.repo.active_plan() is None


def test_active_plan_after_failed_finalization_cannot_be_cancelled(settings, model, monkeypatch):
    runtime = Engine(settings)
    try:
        pending = plan(runtime)
        payload = resume_payload(pending, approved=True)
        with monkeypatch.context() as patcher:
            patcher.setattr(runtime, 'format_result', lambda *args: (_ for _ in ()).throw(ValueError('after graph')))
            with pytest.raises(ValueError, match='after graph'):
                runtime.resume(pending['thread_id'], payload)
    finally:
        runtime.close()
    runtime = Engine(settings)
    try:
        with pytest.raises(WorkflowStateError):
            runtime.cancel(pending['thread_id'])
        assert runtime.repo.workflow(pending['thread_id'])['status'] == 'resume_failed'
        assert runtime.resume(pending['thread_id'], payload)['status'] == 'completed'
        assert runtime.repo.plan(pending['thread_id'])['status'] == 'active'
    finally:
        runtime.close()


def test_plan_cancellation_metadata_is_one_transaction(engine):
    pending = plan(engine)
    engine.repo.conn.executescript("""
        CREATE TRIGGER fail_cancellation BEFORE UPDATE ON workflows
        WHEN NEW.status='cancelled'
        BEGIN SELECT RAISE(ABORT, 'cancel failure'); END;
    """)
    with pytest.raises(sqlite3.IntegrityError, match='cancel failure'):
        engine.cancel(pending['thread_id'])
    assert engine.repo.workflow(pending['thread_id'])['status'] == 'waiting_input'
    assert engine.repo.plan(pending['thread_id'])['status'] == 'pending'


def test_plan_readers_do_not_observe_uncommitted_cancellation(engine):
    pending = plan(engine)
    entered, release = Event(), Event()
    def pause_transaction():
        entered.set()
        assert release.wait(10)
        return 1
    engine.repo.conn.create_function('pause_cancellation', 0, pause_transaction)
    engine.repo.conn.executescript("""
        CREATE TRIGGER pause_then_fail BEFORE UPDATE ON workflows
        WHEN NEW.status='cancelled'
        BEGIN SELECT pause_cancellation(); SELECT RAISE(ABORT, 'rollback cancellation'); END;
    """)
    with ThreadPoolExecutor(max_workers=2) as pool:
        cancelling = pool.submit(engine.cancel, pending['thread_id'])
        try:
            assert entered.wait(3)
            reading = pool.submit(engine.repo.plans)
            with pytest.raises(TimeoutError):
                reading.result(timeout=0.1)
        finally:
            release.set()
        with pytest.raises(sqlite3.IntegrityError, match='rollback cancellation'):
            cancelling.result(timeout=3)
        assert reading.result(timeout=3)[0]['status'] == 'pending'


@pytest.mark.parametrize('kind', ['workflow', 'receipt', 'messages'])
def test_readers_do_not_observe_uncommitted_resume_completion(engine, kind):
    pending = chat(engine, '今天记录体重')
    payload = resume_payload(pending, text='65公斤')
    entered, release = Event(), Event()
    def pause_transaction():
        entered.set()
        assert release.wait(10)
        return 1
    engine.repo.conn.create_function('pause_completion', 0, pause_transaction)
    engine.repo.conn.executescript("""
        CREATE TRIGGER pause_finish AFTER INSERT ON messages
        WHEN NEW.role='assistant'
        BEGIN SELECT pause_completion(); SELECT RAISE(ABORT, 'rollback completion'); END;
    """)
    reads = {
        'workflow': lambda: engine.repo.workflow(pending['thread_id']),
        'receipt': lambda: engine.repo.receipt(payload['request_id']),
        'messages': engine.repo.messages,
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        finishing = pool.submit(engine.resume, pending['thread_id'], payload)
        try:
            assert entered.wait(3)
            reading = pool.submit(reads[kind])
            with pytest.raises(TimeoutError):
                reading.result(timeout=0.1)
        finally:
            release.set()
        with pytest.raises(sqlite3.IntegrityError, match='rollback completion'):
            finishing.result(timeout=3)
        value = reading.result(timeout=3)
    if kind == 'workflow':
        assert value['status'] in ('resuming', 'resume_failed')
    elif kind == 'receipt':
        assert value is None
    else:
        assert not any(item['id'] == payload['request_id'] + ':reply' for item in value)


def test_deletion_and_retention_remove_bound_answers(engine, model):
    pending, payload = conflict_on_patch(engine, model)
    engine.delete_event(engine.repo.events()[0]['id'])
    assert engine.repo.resume_attempt(payload['request_id']) is None
    assert list(engine.checkpointer.list(None)) == []
    pending = chat(engine, '今天记录体重')
    payload = resume_payload(pending, text='65公斤')
    engine.resume(pending['thread_id'], payload)
    engine.repo.prune_workflow_context([{'id': pending['thread_id'], 'kind': 'chat'}], '')
    assert engine.repo.resume_attempt(payload['request_id']) is None


def test_existing_database_migrates_without_losing_health_records(settings, model):
    runtime = Engine(settings)
    try:
        chat(runtime, local=True)
    finally:
        runtime.close()
    with sqlite3.connect(settings.data_dir / 'health.db') as connection:
        connection.execute('DROP TABLE resume_attempts')
        connection.execute('PRAGMA user_version=3')
    runtime = Engine(settings)
    try:
        assert len(runtime.repo.events()) == 1
        assert runtime.repo.conn.execute('PRAGMA user_version').fetchone()[0] == 7
        assert runtime.repo.conn.execute('SELECT COUNT(*) FROM resume_attempts').fetchone()[0] == 0
    finally:
        runtime.close()
