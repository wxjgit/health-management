"""Bound raw input before JSON parsing or running any business operation."""
import asyncio

from starlette.requests import Request

from .errors import error_response
from ..common.errors import ERRORS


class RequestLimitsMiddleware:
    def __init__(self, app, settings):
        self.app, self.settings = app, settings
        self.active = 0

    async def reject(self, scope, receive, send, code):
        spec = ERRORS[code]
        response = error_response(Request(scope), spec.status, code, spec.message)
        response.headers['Connection'] = 'close'
        await response(scope, receive, send)

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = scope.get('headers', [])
        if len(headers) > 64 or sum(len(k) + len(v) for k, v in headers) > 32768 or any(len(k) + len(v) > 8192 for k, v in headers):
            return await self.reject(scope, receive, send, 'request_headers_too_large')
        if any(k.lower() == b'content-encoding' and v.lower() != b'identity' for k, v in headers):
            return await self.reject(scope, receive, send, 'request_encoding_unsupported')
        for k, v in headers:
            if k.lower() == b'content-length' and v.isdigit() and (len(v.lstrip(b'0')) > 12 or int(v.lstrip(b'0') or b'0') > self.settings.request_max_bytes):
                return await self.reject(scope, receive, send, 'request_body_too_large')
        if self.active >= 24:
            return await self.reject(scope, receive, send, 'runtime_busy')
        self.active += 1
        try:
            body = bytearray()
            try:
                async with asyncio.timeout(self.settings.request_body_seconds):
                    while True:
                        message = await receive()
                        if message['type'] == 'http.disconnect':
                            return
                        chunk = message.get('body', b'')
                        if len(body) + len(chunk) > self.settings.request_max_bytes:
                            return await self.reject(scope, receive, send, 'request_body_too_large')
                        body.extend(chunk)
                        if not message.get('more_body', False):
                            break
            except TimeoutError:
                return await self.reject(scope, receive, send, 'request_body_timeout')
            delivered = False

            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
                return await receive()

            await self.app(scope, replay, send)
        finally:
            self.active -= 1
