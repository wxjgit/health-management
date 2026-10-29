"""Bind only server-verified identity before dispatching sync endpoints."""
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request

from .auth import AuthError, bearer
from .errors import error_response
from ..common.errors import AppError, public_error


class UsageMiddleware:
    def __init__(self, app, owner):
        self.app, self.owner = app, owner

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        path = scope.get('path', '')
        if not path.startswith('/api/') or path in (
            '/api/health', '/api/policies', '/api/auth/status', '/api/auth/wechat/login', '/api/auth/logout',
        ):
            return await self.app(scope, receive, send)
        request = Request(scope)
        control = self.owner.state.engine.usage
        try:
            user = await run_in_threadpool(self.owner.state.auth.identify, bearer(request))
            principal = 'user:' + user['id']
            with control.scope(principal):
                await run_in_threadpool(control.request)
                return await self.app(scope, receive, send)
        except AuthError as error:
            response = error_response(request, error.status, error.code, error.message)
        except AppError as error:
            code, spec = public_error(error)
            response = error_response(request, spec.status, code, spec.message)
        await response(scope, receive, send)
