"""Public error responses must be classified, correlated and free of input data."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.common.config import Settings
from app.common.errors import (
    AppError, ConflictError, DomainValidationError, ModelUnavailableError,
    UpstreamModelError, WorkflowStateError, PrivacyCleanupPending,
)
from app.common.request_context import request_id
from app.main import create_app
from app.privacy.deletion import DeletionError
from app.workflows.concurrency import RequestErased, WorkflowConflict
from app.api.errors import safe_preview
from app.api.request_context import RequestContextMiddleware


SECRET = 'synthetic-health-and-key-never-public'


class ProbeBody(BaseModel):
    text: str


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, local_only=True, scheduler_enabled=False))

    @app.get('/api/_probe/error/{kind}')
    async def fail(kind: str):
        errors = {
            'domain': DomainValidationError(),
            'conflict': ConflictError(),
            'state': WorkflowStateError(),
            'workflow': WorkflowConflict(),
            'unavailable': ModelUnavailableError(),
            'privacy': PrivacyCleanupPending(),
            'upstream': UpstreamModelError(),
            'value': ValueError(SECRET),
            'type': TypeError(SECRET),
            'runtime': RuntimeError(SECRET),
            'mismatch': DomainValidationError(code='model_unavailable'),
            'unknown_code': AppError(code=SECRET),
            'erased': RequestErased(),
            'http': HTTPException(403, detail=SECRET, headers={'Secret': SECRET}),
            'deletion': DeletionError('preview_required', SECRET, 428),
            'invalid_deletion': DeletionError(SECRET, SECRET, 409),
        }
        raise errors[kind]

    @app.post('/api/_probe/body')
    async def body(value: ProbeBody):
        return {'ok': True}

    @app.get('/api/_probe/correlation')
    async def correlation():
        before = request_id.get()
        await asyncio.sleep(0.01)
        return {'before': before, 'after': request_id.get()}

    # The optional built frontend installs a catch-all; probes precede that route.
    app.router.routes.sort(key=lambda route: getattr(route, 'path', '') == '/{path:path}')
    with TestClient(app, raise_server_exceptions=False) as session:
        yield session


@pytest.mark.parametrize(('kind', 'status', 'code'), [
    ('domain', 422, 'domain_validation_failed'),
    ('conflict', 409, 'resource_version_conflict'),
    ('state', 409, 'workflow_state_invalid'),
    ('workflow', 409, 'workflow_execution_conflict'),
    ('unavailable', 503, 'model_unavailable'),
    ('privacy', 503, 'privacy_cleanup_pending'),
    ('upstream', 502, 'model_upstream_failed'),
    ('value', 500, 'internal_error'),
    ('type', 500, 'internal_error'),
    ('runtime', 500, 'internal_error'),
    ('mismatch', 500, 'internal_error'),
    ('unknown_code', 500, 'internal_error'),
    ('erased', 410, 'request_erased'),
    ('http', 403, 'http_403'),
    ('deletion', 428, 'preview_required'),
    ('invalid_deletion', 500, 'internal_error'),
])
def test_error_envelope_and_logs_are_safe(client, caplog, kind, status, code):
    response = client.get(f'/api/_probe/error/{kind}', headers={
        'Origin': 'http://localhost:5173', 'X-Request-ID': SECRET,
    })
    assert response.status_code == status
    body = response.json()
    assert body['code'] == code
    assert str(UUID(body['request_id'])) == response.headers['x-request-id']
    assert SECRET not in response.text
    assert SECRET not in str(dict(response.headers))
    assert SECRET not in caplog.text
    assert response.headers['access-control-allow-origin'] == 'http://localhost:5173'
    assert 'X-Request-ID' in response.headers['access-control-expose-headers']
    assert body['request_id'] in caplog.text


@pytest.mark.parametrize('payload', [
    {'text': {'health': SECRET}}, {}, {'text': [SECRET]},
])
def test_validation_does_not_echo_input(client, payload):
    response = client.post('/api/_probe/body', json=payload)
    assert response.status_code == 422
    assert response.json()['code'] == 'request_validation_failed'
    assert SECRET not in response.text
    assert all(set(item) == {'field', 'code'} for item in response.json()['errors'])
    assert response.json()['request_id'] == response.headers['x-request-id']


def test_unknown_validation_key_and_invalid_json_are_not_echoed(client):
    for kwargs in (
        {'json': {'request_id': str(uuid4()), 'text': '今天体重65公斤', SECRET: SECRET}},
        {'content': '{' + SECRET, 'headers': {'Content-Type': 'application/json'}},
    ):
        response = client.post('/api/chat', **kwargs)
        assert response.status_code == 422
        assert SECRET not in response.text
        assert response.json()['request_id'] == response.headers['x-request-id']


def test_404_405_and_success_headers(client):
    missing = client.get('/api/_missing')
    assert missing.status_code == 404
    assert missing.json()['code'] == 'http_404'
    method = client.post('/api/profile')
    assert method.status_code == 405
    assert method.json()['code'] == 'http_405'
    assert set(method.headers['allow'].split(', ')) <= {'GET', 'PUT'}
    success = client.get('/api/health')
    assert success.status_code == 200
    assert 'request_id' not in success.json()
    UUID(success.headers['x-request-id'])


def test_correlation_is_isolated_and_server_generated(client):
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: client.get('/api/_probe/correlation', headers={
            'X-Request-ID': 'client-input',
        }), range(8)))
    ids = set()
    for response in responses:
        identifier = response.headers['x-request-id']
        assert response.json() == {'before': identifier, 'after': identifier}
        UUID(identifier)
        ids.add(identifier)
    assert len(ids) == 8
    assert request_id.get() is None


def test_500_does_not_expand_cors_allowlist(client):
    response = client.get('/api/_probe/error/value', headers={'Origin': 'https://unknown.invalid'})
    assert response.status_code == 500
    assert 'access-control-allow-origin' not in response.headers


def test_business_request_id_is_preserved_during_retry(client):
    business_id = str(uuid4())
    payload = {'request_id': business_id, 'text': '今天体重65公斤', 'processing_mode': 'local_only'}
    first = client.post('/api/chat', json=payload)
    second = client.post('/api/chat', json=payload)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.headers['x-request-id'] != second.headers['x-request-id']
    assert client.app.state.engine.repo.workflow_by_request(business_id) is not None
    assert len(client.get('/api/events').json()) == 1


def test_preflight_success_and_failure_have_request_ids(client):
    for origin, status in [('http://localhost:5173', 200), ('https://unknown.invalid', 400)]:
        response = client.options('/api/chat', headers={
            'Origin': origin, 'Access-Control-Request-Method': 'POST',
        })
        assert response.status_code == status
        UUID(response.headers['x-request-id'])
        if status == 400:
            assert response.json()['code'] == 'http_400'
            assert response.json()['request_id'] == response.headers['x-request-id']


@pytest.mark.parametrize('suffix', ['', ':fact', ':fact:2'])
def test_safe_preview_preserves_metadata_and_discards_health_fields(suffix):
    target, preview_id = str(uuid4()) + suffix, str(uuid4())
    safe = {'target_id': target, 'preview_id': preview_id, 'target_type': 'event',
            'scope': 'dependencies', 'counts': {'event:delete': 1}, 'reasons': []}
    unsafe = {**safe, 'raw_text': SECRET, 'content': SECRET,
              'reasons': [SECRET], 'counts': {**safe['counts'], SECRET: 2, 'fact:delete': SECRET}}
    assert safe_preview(unsafe) == safe


def test_only_completed_safe_errors_suppress_server_exception_logging():
    async def check(safe, complete):
        async def inner(scope, receive, send):
            if safe:
                scope['state']['safe_error_response'] = True
            await send({'type': 'http.response.start', 'status': 500, 'headers': []})
            if complete:
                await send({'type': 'http.response.body', 'body': b'{}'})
            raise ValueError(SECRET)

        async def receive():
            return {'type': 'http.request', 'body': b''}

        async def send(message):
            pass

        await RequestContextMiddleware(inner)({'type': 'http'}, receive, send)

    asyncio.run(check(True, True))
    for safe, complete in [(False, True), (True, False)]:
        with pytest.raises(ValueError):
            asyncio.run(check(safe, complete))
    assert request_id.get() is None
