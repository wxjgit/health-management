import asyncio
import json

import httpx
import pytest

from app.common.config import Settings
from app.common.errors import ExecutionDeadlineExceeded, ModelUnavailableError, UpstreamModelError
from app.common.execution_budget import RuntimeLimiter
from app.modeling.transport import request_model


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks, delay=0):
        self.chunks, self.delay, self.closed = chunks, delay, False

    async def __aiter__(self):
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield chunk

    async def aclose(self):
        self.closed = True


def install(monkeypatch, response, captured):
    original = httpx.AsyncClient

    def factory(**kwargs):
        captured.append(kwargs)
        return original(**kwargs, transport=httpx.MockTransport(lambda request: response))

    monkeypatch.setattr('app.modeling.transport.httpx.AsyncClient', factory)


def settings(**kwargs):
    return Settings(model_base_url='https://test.invalid/v1', model_name='test', model_api_key='synthetic-only', **kwargs)


def test_exact_response_limit_and_no_environment_proxy(monkeypatch):
    captured = []
    stream = Chunks([b'{"ok":', b'true}'])
    install(monkeypatch, httpx.Response(200, stream=stream), captured)
    assert request_model(settings(model_response_max_bytes=11), {}, 'processing_plan') == {'ok': True}
    assert stream.closed
    assert captured[0]['trust_env'] is False
    assert captured[0]['follow_redirects'] is False


@pytest.mark.parametrize('length', [None, '99999999', '1'])
def test_response_size_is_checked_while_streaming(monkeypatch, length):
    stream = Chunks([b'a' * 8, b'b' * 8, b'never-read'])
    headers = {'content-length': length} if length else {}
    install(monkeypatch, httpx.Response(200, headers=headers, stream=stream), [])
    with pytest.raises(UpstreamModelError) as error:
        request_model(settings(model_response_max_bytes=10), {}, 'processing_plan')
    assert error.value.code == 'model_response_too_large'
    assert stream.closed


def test_upstream_compression_is_rejected_without_decompression(monkeypatch):
    stream = Chunks([b'not-read'])
    install(monkeypatch, httpx.Response(200, headers={'content-encoding': 'gzip'}, stream=stream), [])
    with pytest.raises(UpstreamModelError):
        request_model(settings(), {}, 'processing_plan')
    assert stream.closed


def test_non_success_body_is_never_buffered(monkeypatch):
    stream = Chunks([b'a' * 10000000])
    install(monkeypatch, httpx.Response(401, stream=stream), [])
    with pytest.raises(UpstreamModelError):
        request_model(settings(), {}, 'processing_plan')
    assert stream.closed


def test_continuous_drip_is_canceled_at_absolute_timeout(monkeypatch):
    stream = Chunks([b' '] * 1000, delay=0.01)
    install(monkeypatch, httpx.Response(200, stream=stream), [])
    with pytest.raises(ModelUnavailableError):
        request_model(settings(model_timeout_seconds=0.03), {}, 'processing_plan')
    assert stream.closed


def test_execution_deadline_is_not_swallowed_as_model_failure(monkeypatch):
    stream = Chunks([b' '] * 1000, delay=0.01)
    install(monkeypatch, httpx.Response(200, stream=stream), [])
    runtime = RuntimeLimiter(Settings(execution_seconds=0.025))
    with pytest.raises(ExecutionDeadlineExceeded):
        with runtime.execution():
            request_model(settings(), {}, 'processing_plan')
    assert stream.closed
