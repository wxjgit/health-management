"""Wrap even ServerErrorMiddleware so correlation and CORS cover HTTP 500."""

from uuid import uuid4

from fastapi import FastAPI
from starlette.datastructures import MutableHeaders
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from ..common.request_context import log_failure, request_id


class RequestContextMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        correlation = str(uuid4())
        scope.setdefault('state', {})['request_id'] = correlation
        token = request_id.set(correlation)
        complete = False

        async def correlated_send(message):
            nonlocal complete
            if message['type'] == 'http.response.start':
                MutableHeaders(scope=message)['X-Request-ID'] = correlation
            await send(message)
            if message['type'] == 'http.response.body' and not message.get('more_body', False):
                complete = True

        try:
            await self.app(scope, receive, correlated_send)
        except Exception:
            # Starlette rethrows handled 500s after sending the response. Prevent
            # the ASGI server from logging raw health input / upstream exception
            # chains. Never hide errors before our safe response was completed.
            if not (complete and scope['state'].get('safe_error_response')):
                raise
        finally:
            request_id.reset(token)


class CorrelatedCORSMiddleware(CORSMiddleware):
    def preflight_response(self, request_headers):
        response = super().preflight_response(request_headers)
        if response.status_code == 400:
            log_failure('http_400', status=400)
            return JSONResponse(status_code=400, content={
                'detail': '跨域请求不符合允许范围。', 'code': 'http_400',
                'request_id': request_id.get(),
            }, headers={key: value for key, value in response.headers.items()
                        if key.startswith('access-control-') or key == 'vary'})
        return response


class RequestContextFastAPI(FastAPI):
    def build_middleware_stack(self):
        # Keep the FastAPI object (state, dependency overrides and lifespan) public.
        return RequestContextMiddleware(CorrelatedCORSMiddleware(
            super().build_middleware_stack(),
            allow_origins=['http://localhost:5173', 'http://127.0.0.1:5173'],
            allow_methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'],
            allow_headers=['Content-Type'],
            expose_headers=['X-Request-ID'],
        ))
