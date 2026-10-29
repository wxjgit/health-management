"""Wrap even ServerErrorMiddleware so correlation covers HTTP 500."""

from uuid import uuid4
import time

from fastapi import FastAPI
from starlette.datastructures import MutableHeaders

from ..common.request_context import request_id
from .request_limits import RequestLimitsMiddleware
from .usage import UsageMiddleware


class RequestContextMiddleware:
    def __init__(self, app, owner=None):
        self.app = app
        self.owner = owner

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        correlation = str(uuid4())
        scope.setdefault('state', {})['request_id'] = correlation
        token = request_id.set(correlation)
        from ..observability.context import current, operation_id, thread_link
        service = getattr(getattr(self.owner, 'state', None), 'engine', None)
        service = getattr(service, 'observability', None)
        context_tokens = [(current, current.set(service)), (operation_id, operation_id.set(None)),
                          (thread_link, thread_link.set(None))]
        started = time.monotonic()
        status, outcome = None, 'ok'
        complete = False
        disconnected = False

        async def observed_receive():
            nonlocal disconnected
            message = await receive()
            if message['type'] == 'http.disconnect':
                disconnected = True
            return message

        async def correlated_send(message):
            nonlocal complete, status
            if message['type'] == 'http.response.start':
                status = message['status']
                MutableHeaders(scope=message)['X-Request-ID'] = correlation
            await send(message)
            if message['type'] == 'http.response.body' and not message.get('more_body', False):
                complete = True

        try:
            await self.app(scope, observed_receive, correlated_send)
        except BaseException as error:
            import asyncio
            outcome = 'cancelled' if isinstance(error, asyncio.CancelledError) else 'error'
            # Starlette rethrows handled 500s after sending the response. Prevent
            # the ASGI server from logging raw health input / upstream exception
            # chains. Never hide errors before our safe response was completed.
            if not isinstance(error, Exception) or not (complete and scope['state'].get('safe_error_response')):
                raise
        finally:
            if service:
                from ..observability.contracts import status_class
                path = getattr(scope.get('route'), 'path', 'unmatched')
                route = path if path in service.routes else 'unmatched'
                method = scope.get('method')
                service.emit('http_finished', route=route,
                    method=method if method in {'GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS'} else 'other',
                    status_class=status_class(status), outcome='disconnected' if disconnected and not complete else outcome if outcome != 'ok' or complete else 'incomplete',
                    duration_seconds=time.monotonic()-started)
            for var, context_token in reversed(context_tokens):
                var.reset(context_token)
            request_id.reset(token)


class RequestContextFastAPI(FastAPI):
    def build_middleware_stack(self):
        # Keep the FastAPI object (state, dependency overrides and lifespan) public.
        return RequestContextMiddleware(
            RequestLimitsMiddleware(UsageMiddleware(super().build_middleware_stack(), self), self.state.settings),
            owner=self)
