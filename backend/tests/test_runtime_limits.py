import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import json
import sqlite3
from threading import Event
import time
from uuid import UUID

import pytest

from app.api.request_context import RequestContextMiddleware
from app.api.request_limits import RequestLimitsMiddleware
from app.common.config import Settings
from app.common.errors import ExecutionDeadlineExceeded, RuntimeLimitError
from app.common.execution_budget import BudgetLock, RuntimeLimiter, check_budget
from app.infrastructure.budget_connection import BudgetConnection


def request(chunks, *, headers=(), limit=8, delay=0, seconds=1):
    sent, called = [], []

    async def receive():
        if delay:
            await asyncio.sleep(delay)
        return chunks.pop(0)

    async def send(message):
        sent.append(message)

    async def inner(scope, receive, send):
        called.append((await receive())['body'])
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b'ok'})

    middleware = RequestContextMiddleware(RequestLimitsMiddleware(inner,
        Settings(request_max_bytes=limit, request_body_seconds=seconds)))
    asyncio.run(middleware({'type': 'http', 'method': 'POST', 'path': '/', 'headers': list(headers)}, receive, send))
    return sent, called


@pytest.mark.parametrize('size,status', [(8, 200), (9, 413)])
def test_actual_body_boundary_and_correlation(size, status):
    sent, called = request([{'type': 'http.request', 'body': b'a' * size}])
    assert sent[0]['status'] == status
    UUID(dict(sent[0]['headers'])[b'x-request-id'].decode())
    assert len(called) == (status == 200)


def test_chunked_and_forged_content_length_do_not_bypass_limit():
    sent, called = request([{'type': 'http.request', 'body': b'a' * 5, 'more_body': True},
                            {'type': 'http.request', 'body': b'b' * 4}], headers=[(b'content-length', b'1')])
    assert sent[0]['status'] == 413 and not called


def test_known_oversize_is_rejected_before_receiving():
    sent, called = request([], headers=[(b'content-length', b'9')])
    assert sent[0]['status'] == 413 and not called


def test_long_content_length_does_not_escape_safe_limit_errors():
    sent, called = request([], headers=[(b'content-length', b'9' * 5000)])
    assert sent[0]['status'] == 413 and not called
    sent, called = request([{'type': 'http.request', 'body': b''}],
                           headers=[(b'content-length', b'0' * 5000)])
    assert sent[0]['status'] == 200 and called == [b'']


def test_delete_route_reserves_management_before_looking_up_preview(tmp_path):
    from app.api.deletions import DeleteRequest, execute
    from app.workflows.engine import Engine

    engine = Engine(Settings(data_dir=tmp_path, scheduler_enabled=False), user_id='00000000-0000-4000-8000-000000000001')
    entered, release = Event(), Event()

    def occupied():
        with engine.runtime.execution('management'):
            entered.set()
            release.wait(2)

    try:
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(occupied)
            assert entered.wait(1)
            try:
                with pytest.raises(RuntimeLimitError) as error:
                    execute(DeleteRequest(preview_id='synthetic-missing', request_id='synthetic-request'), engine)
                assert error.value.code == 'runtime_busy'
            finally:
                release.set()
            future.result(timeout=2)
    finally:
        engine.close()


def test_compressed_and_huge_headers_are_rejected_before_business():
    for headers, status in [([(b'content-encoding', b'gzip')], 415), ([(b'x', b'a' * 8193)], 431)]:
        sent, called = request([], headers=headers)
        assert sent[0]['status'] == status and not called


def test_drip_feed_uses_absolute_body_deadline():
    sent, called = request([{'type': 'http.request', 'body': b'a', 'more_body': True}] * 10,
                           delay=0.02, seconds=0.035)
    assert sent[0]['status'] == 408 and not called
    assert json.loads(sent[-1]['body'])['code'] == 'request_body_timeout'


def test_disconnect_does_not_start_business():
    sent, called = request([{'type': 'http.disconnect'}])
    assert not sent and not called


def test_global_admission_and_management_capacity_are_independent():
    runtime = RuntimeLimiter(Settings(runtime_max_executions=1))
    entered, release = Event(), Event()

    def slow():
        with runtime.execution():
            entered.set()
            assert release.wait(2)

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(slow)
        assert entered.wait(1)
        try:
            with pytest.raises(RuntimeLimitError):
                with runtime.execution():
                    pytest.fail('capacity bypass')
            with runtime.execution('management'):
                check_budget()
            assert len(runtime.snapshot()['deadlines']) == 1
        finally:
            release.set()
        future.result(timeout=2)
    assert runtime.snapshot()['deadlines'] == []


def test_nested_execution_cannot_reset_deadline_or_leak_slot():
    runtime = RuntimeLimiter(Settings(execution_seconds=0.015, runtime_max_executions=1))
    with pytest.raises(ExecutionDeadlineExceeded):
        with runtime.execution():
            time.sleep(0.025)
            with runtime.execution():
                pytest.fail('deadline reset')
    with runtime.execution():
        check_budget()


def test_lock_wait_is_bounded_and_expired_transaction_rolls_back(tmp_path):
    runtime = RuntimeLimiter(Settings(execution_seconds=0.025))
    lock = BudgetLock()
    entered, release = Event(), Event()

    def hold():
        with lock:
            entered.set()
            release.wait(2)

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(hold)
        assert entered.wait(1)
        try:
            with pytest.raises(ExecutionDeadlineExceeded):
                with runtime.execution(), lock:
                    pytest.fail('lock was not bounded')
        finally:
            release.set()
        future.result(timeout=2)
    conn = sqlite3.connect(tmp_path / 'test.db', factory=BudgetConnection)
    conn.execute('CREATE TABLE records(value INTEGER)')
    with pytest.raises(ExecutionDeadlineExceeded):
        with runtime.execution(), conn:
            conn.execute('INSERT INTO records VALUES (1)')
            time.sleep(0.035)
    assert conn.execute('SELECT COUNT(*) FROM records').fetchone()[0] == 0
    conn.close()


def test_deadline_propagates_to_graph_style_worker_context():
    runtime = RuntimeLimiter(Settings(execution_seconds=0.01))
    with pytest.raises(ExecutionDeadlineExceeded):
        with runtime.execution():
            context = copy_context()
            time.sleep(0.02)
            with ThreadPoolExecutor(1) as pool:
                pool.submit(context.run, check_budget).result(timeout=1)
