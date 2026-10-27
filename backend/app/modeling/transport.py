"""Cancelable, bounded model transport. It never receives a repository."""
import asyncio
import json
import time

import httpx

from ..common.execution_budget import check_budget, remaining_seconds
from ..common.errors import ModelUnavailableError, UpstreamModelError


async def fetch_model(settings, body, purpose):
    from ..observability.context import emit
    from ..observability.spans import error_outcome
    started = time.monotonic()
    observation = {'status_class': 'none', 'outcome': 'ok', 'stage': 'none'}
    try:
        return await _fetch_model(settings, body, purpose, observation)
    except BaseException as error:
        cause = error.__cause__
        if isinstance(error, asyncio.CancelledError):
            observation['outcome'] = 'cancelled'
        elif error_outcome(error) == 'deadline':
            observation['outcome'] = 'deadline'
        elif isinstance(cause, (TimeoutError, httpx.TimeoutException)):
            observation['outcome'] = 'timeout'
            observation['stage'] = next((stage for typ, stage in (
                (httpx.ConnectTimeout, 'connect'), (httpx.ReadTimeout, 'read'),
                (httpx.WriteTimeout, 'write'), (httpx.PoolTimeout, 'pool')) if isinstance(cause, typ)), 'overall')
        elif isinstance(cause, httpx.ProxyError):
            observation['outcome'] = 'proxy_error'
        elif isinstance(cause, httpx.NetworkError):
            observation['outcome'] = 'network_error'
        elif getattr(error, 'code', None) == 'model_response_too_large':
            observation['outcome'] = 'response_too_large'
        elif getattr(error, 'code', None) == 'model_response_invalid':
            observation['outcome'] = 'response_invalid'
        elif observation['status_class'] not in ('none', '2xx'):
            observation['outcome'] = 'http_error'
        else:
            observation['outcome'] = 'error'
        raise
    finally:
        emit('model_transport_finished', purpose=purpose, duration_seconds=time.monotonic()-started, **observation)


async def _fetch_model(settings, body, purpose, observation):
    if settings.runtime_profile == 'hardened':
        settings.validate_model_target()
    maximum = settings.safety_timeout_seconds if purpose == 'safety_classification' else settings.model_timeout_seconds
    seconds = remaining_seconds(maximum)
    try:
        async with asyncio.timeout(seconds):
            async with httpx.AsyncClient(timeout=httpx.Timeout(seconds, connect=min(5, seconds)),
                                         follow_redirects=False, trust_env=False,
                                         proxy=settings.model_egress_proxy or None,
                                         limits=httpx.Limits(max_connections=2, max_keepalive_connections=0)) as client:
                async with client.stream('POST', settings.model_base_url + '/chat/completions',
                                         headers={'Authorization': 'Bearer ' + settings.model_api_key,
                                                  'Accept-Encoding': 'identity'}, json=body) as response:
                    from ..observability.contracts import status_class
                    observation['status_class'] = status_class(response.status_code)
                    if response.status_code in (429, 503):
                        raise ModelUnavailableError(code='model_rate_limited' if response.status_code == 429
                                                    else 'model_service_unavailable')
                    if not response.is_success:
                        raise UpstreamModelError()
                    if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                        raise UpstreamModelError(code='model_response_invalid')
                    length = response.headers.get('content-length', '')
                    if length.isascii() and length.isdigit() and (len(length.lstrip('0')) > 12 or int(length.lstrip('0') or '0') > settings.model_response_max_bytes):
                        raise UpstreamModelError(code='model_response_too_large')
                    payload = bytearray()
                    # HTTPX MockTransport may return an already-consumed response.
                    if response.is_stream_consumed:
                        if len(response.content) > settings.model_response_max_bytes:
                            raise UpstreamModelError(code='model_response_too_large')
                        payload.extend(response.content)
                    else:
                        async for chunk in response.aiter_raw():
                            check_budget()
                            if len(payload) + len(chunk) > settings.model_response_max_bytes:
                                raise UpstreamModelError(code='model_response_too_large')
                            payload.extend(chunk)
                    check_budget()
                    try:
                        return json.loads(payload)
                    except (ValueError, UnicodeError) as error:
                        raise UpstreamModelError(code='model_response_invalid') from error
    except (TimeoutError, httpx.TimeoutException, httpx.NetworkError, httpx.ProxyError) as error:
        check_budget()
        raise ModelUnavailableError() from error
    except httpx.RequestError as error:
        raise UpstreamModelError() from error


def request_model(settings, body, purpose):
    check_budget()
    return asyncio.run(fetch_model(settings, body, purpose))
