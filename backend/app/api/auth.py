"""WeChat identities and revocable opaque sessions, separate from legacy health data."""
import asyncio
from collections import deque
from threading import Lock
import os
import json
from typing import Literal
from contextlib import contextmanager
import hashlib
import logging
import re
import secrets
import sqlite3
import time
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from .errors import error_response
from .policies import policy


class WechatLogFilter(logging.Filter):
    def filter(self, record):
        # httpx INFO logs include the full URL, and WeChat requires secrets in
        # query parameters. Drop this request's transport log entirely.
        return 'jscode2session' not in record.getMessage()


logging.getLogger('httpx').addFilter(WechatLogFilter())


class AuthError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


async def auth_error(request, error):
    return error_response(request, error.status, error.code, error.message)


class AuthStore:
    def __init__(self, settings):
        self.settings = settings
        self.login_times = deque()
        self.login_clients = {}
        self.login_lock = Lock()
        self.path = settings.data_dir / 'accounts.db'
        self.deletions_path = settings.data_dir / 'account-deletions.jsonl'
        self.deletions_path.touch(mode=0o600, exist_ok=True)
        with self.connect() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, app_id TEXT NOT NULL, openid TEXT NOT NULL,
                    created_at INTEGER NOT NULL, UNIQUE(app_id, openid));
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    expires_at INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
                CREATE TABLE IF NOT EXISTS login_codes (
                    code_hash TEXT PRIMARY KEY, expires_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS account_deletions (
                    user_id TEXT PRIMARY KEY REFERENCES users(id), requested_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS user_consents (
                    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    version TEXT NOT NULL, model_allowed INTEGER NOT NULL, updated_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS consent_events (
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    version TEXT NOT NULL, model_allowed INTEGER NOT NULL, created_at INTEGER NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def digest(value):
        return hashlib.sha256(value.encode()).hexdigest()

    def limit_login(self, client='unknown'):
        # Never trust a forwarded IP header. The TLS proxy/gateway must set the
        # client address through a trusted hop, or the limit safely groups it.
        now = time.monotonic()
        with self.login_lock:
            while self.login_times and self.login_times[0] <= now - 60:
                self.login_times.popleft()
            self.login_clients = {key: times for key, times in self.login_clients.items() if times and times[-1] > now - 60}
            times = self.login_clients.setdefault(client, deque())
            while times and times[0] <= now - 60:
                times.popleft()
            if len(self.login_times) >= self.settings.login_global_per_minute or len(times) >= self.settings.login_client_per_minute:
                raise AuthError(429, 'login_rate_limited', '登录请求较多，请稍后再试。')
            self.login_times.append(now)
            times.append(now)

    def consent(self, user_id):
        with self.connect() as conn:
            row = conn.execute('SELECT * FROM user_consents WHERE user_id=? AND user_id NOT IN (SELECT user_id FROM account_deletions)', (user_id,)).fetchone()
        current = policy(self.settings)
        valid = bool(row and row['version'] == current['version'])
        return {'version': current['version'], 'privacy_accepted': valid,
                'model_allowed': bool(valid and row['model_allowed'] and current['model_available'])}

    def save_consent(self, user_id, version, model_allowed):
        current = policy(self.settings)
        if version != current['version'] or not current['ready']:
            raise AuthError(428, 'privacy_consent_required', '请阅读并同意当前版本的隐私政策和健康信息处理说明。')
        allowed = bool(model_allowed and current['model_available'])
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if not conn.execute('SELECT 1 FROM users WHERE id=? AND id NOT IN (SELECT user_id FROM account_deletions)', (user_id,)).fetchone():
                raise AuthError(409, 'account_deleting', '账号不可用或正在注销。')
            conn.execute('INSERT INTO user_consents VALUES (?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET version=excluded.version,model_allowed=excluded.model_allowed,updated_at=excluded.updated_at',
                         (user_id, version, int(allowed), int(time.time())))
            conn.execute('INSERT INTO consent_events VALUES (?,?,?,?)', (user_id, version, int(allowed), int(time.time())))
        return self.consent(user_id)

    def require_privacy(self, user_id):
        if not self.consent(user_id)['privacy_accepted']:
            raise AuthError(428, 'privacy_consent_required', '隐私说明已更新，请重新阅读并同意后使用。')

    def require_model(self, user_id):
        if not self.consent(user_id)['model_allowed']:
            from ..common.errors import RuntimeLimitError
            raise RuntimeLimitError(code='model_consent_required')

    def request_deletion(self, user_id):
        with self.connect() as conn:
            conn.execute('INSERT OR IGNORE INTO account_deletions VALUES (?,?)', (user_id, int(time.time())))
            conn.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))

    def finish_deletion(self, user_id):
        # Journal must reach disk before removing identity; restores need this
        # newest journal, not just the older one contained in a backup.
        with self.deletions_path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps({'user_id': user_id, 'deleted_at': int(time.time())}) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        with self.connect() as conn:
            conn.execute('DELETE FROM account_deletions WHERE user_id=?', (user_id,))
            conn.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
            conn.execute('DELETE FROM users WHERE id=?', (user_id,))

    def claim_code(self, code):
        # Claim before network I/O: concurrent/replayed codes cannot mint sessions.
        now = int(time.time())
        with self.connect() as conn:
            conn.execute('DELETE FROM login_codes WHERE expires_at<=?', (now,))
            try:
                conn.execute('INSERT INTO login_codes VALUES (?,?)', (self.digest(code), now + 600))
            except sqlite3.IntegrityError:
                raise AuthError(401, 'login_code_used', '登录凭证已使用，请重新微信登录。') from None

    def issue(self, openid):
        now = int(time.time())
        token = secrets.token_urlsafe(32)
        expires = now + self.settings.auth_session_seconds
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('INSERT OR IGNORE INTO users VALUES (?,?,?,?)',
                         (str(uuid4()), self.settings.wechat_app_id, openid, now))
            user = conn.execute('SELECT id FROM users WHERE app_id=? AND openid=?',
                                (self.settings.wechat_app_id, openid)).fetchone()
            if conn.execute('SELECT 1 FROM account_deletions WHERE user_id=?', (user['id'],)).fetchone():
                raise AuthError(409, 'account_deleting', '账号正在注销清理，请稍后再试。')
            conn.execute('DELETE FROM sessions WHERE expires_at<=?', (now,))
            conn.execute('INSERT INTO sessions VALUES (?,?,?)', (self.digest(token), user['id'], expires))
        return {'access_token': token, 'token_type': 'Bearer', 'expires_at': expires,
                'user': {'id': user['id']}}

    def identify(self, token):
        with self.connect() as conn:
            row = conn.execute('SELECT user_id FROM sessions WHERE token_hash=? AND expires_at>? AND user_id NOT IN (SELECT user_id FROM account_deletions)',
                               (self.digest(token), int(time.time()))).fetchone()
        if row is None:
            raise AuthError(401, 'login_required', '登录已过期，请重新微信登录。')
        return {'id': row['user_id']}

    def revoke(self, token):
        with self.connect() as conn:
            conn.execute('DELETE FROM sessions WHERE token_hash=?', (self.digest(token),))


def bearer(request):
    value = request.headers.get('authorization', '')
    if not re.fullmatch(r'Bearer [A-Za-z0-9_-]{43}', value):
        raise AuthError(401, 'login_required', '请先微信登录。')
    return value[7:]


def require_user(request: Request):
    store = request.app.state.auth
    user = store.identify(bearer(request))
    request.state.user = user
    return user


async def exchange_code(settings, code):
    # No environment proxy or redirects: never forward AppSecret to another host.
    proxy = settings.model_egress_proxy if settings.runtime_profile == 'hardened' else None
    payload = None
    # 传输层瞬时故障（连接重置/超时/5xx/非 JSON）自动重试一次：
    # 失败的交换不消费 code（微信仅对成功交换作废 code），重试安全；
    # 业务 errcode（40029/40125 等）不重试，直接走原有分支。
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=8, trust_env=False, proxy=proxy) as client:
                response = await client.get('https://api.weixin.qq.com/sns/jscode2session', params={
                    'appid': settings.wechat_app_id, 'secret': settings.wechat_app_secret,
                    'js_code': code, 'grant_type': 'authorization_code',
                })
                response.raise_for_status()
                payload = response.json()
            break
        except (httpx.HTTPError, ValueError):
            if attempt == 0:
                logging.getLogger(__name__).warning('wechat code2session transient failure, retrying')
                await asyncio.sleep(0.6)
                continue
            raise AuthError(502, 'wechat_unavailable', '微信登录暂时不可用，请重新登录。') from None
    if not isinstance(payload, dict):
        raise AuthError(502, 'wechat_unavailable', '微信登录暂时不可用，请重新登录。')
    if payload.get('errcode'):
        raise AuthError(401, 'wechat_login_failed', '微信登录失败，请重新登录。')
    openid = payload.get('openid')
    if not isinstance(openid, str) or not 1 <= len(openid) <= 128 or not isinstance(payload.get('session_key'), str) or not payload['session_key']:
        raise AuthError(502, 'wechat_unavailable', '微信登录暂时不可用，请重新登录。')
    return openid


class LoginInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    privacy_version: str = Field(min_length=64, max_length=64)
    privacy_consent: Literal[True]
    health_consent: Literal[True]
    model_consent: bool = False
    code: str = Field(min_length=1, max_length=256, pattern=r'^[A-Za-z0-9_-]+$')


router = APIRouter(prefix='/api/auth', tags=['auth'])


@router.get('/status')
def status(request: Request):
    return {'enabled': True, 'business_ready': True}


@router.post('/wechat/login')
async def login(body: LoginInput, request: Request):
    store = request.app.state.auth
    current = policy(store.settings)
    if body.privacy_version != current['version'] or not current['ready']:
        raise AuthError(428, 'privacy_consent_required', '请阅读并同意当前版本的隐私说明。')
    client = request.client.host if request.client else 'unknown'
    if store.settings.runtime_profile == 'hardened' and client == '172.30.80.2':
        import ipaddress
        try:
            client = str(ipaddress.ip_address(request.headers.get('x-qingji-client', '')))
        except ValueError:
            client = 'trusted-gateway'
    store.limit_login(client)
    store.claim_code(body.code)
    openid = await exchange_code(store.settings, body.code)
    result = store.issue(openid)
    result['consent'] = store.save_consent(result['user']['id'], body.privacy_version, body.model_consent)
    return result


@router.get('/me')
def me(user=Depends(require_user)):
    return {'user': user}


@router.get('/usage')
def usage(request: Request, user=Depends(require_user)):
    return request.app.state.engine.usage.snapshot()


class ConsentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    privacy_version: str
    privacy_consent: Literal[True]
    health_consent: Literal[True]
    model_consent: bool = False


@router.get('/consent')
def consent_status(request: Request, user=Depends(require_user)):
    return request.app.state.auth.consent(user['id'])


@router.put('/consent')
def consent_update(body: ConsentInput, request: Request, user=Depends(require_user)):
    return request.app.state.auth.save_consent(user['id'], body.privacy_version, body.model_consent)


class CloseAccountInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirmation: Literal['注销账号']


@router.delete('/account')
def close_account(body: CloseAccountInput, request: Request, user=Depends(require_user)):
    request.app.state.auth.request_deletion(user['id'])
    try:
        completed = request.app.state.engine.purge(user['id'])
    except OSError:
        completed = False
    return {'state': 'completed' if completed else 'pending'}


@router.post('/logout')
def logout(request: Request, user=Depends(require_user)):
    request.app.state.auth.revoke(bearer(request))
    return {'ok': True}


def business_access(request: Request):
    if request.url.path == '/api/health':
        return
    if request.url.path.startswith('/internal/'):
        from fastapi import HTTPException
        raise HTTPException(404)
    user = require_user(request)
    request.app.state.auth.require_privacy(user['id'])
