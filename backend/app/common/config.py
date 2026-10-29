"""Explicit runtime configuration. Configuration failures never include values."""
from dataclasses import dataclass, field
from pathlib import Path
import ipaddress
import math
import os
import re
from urllib.parse import urlsplit

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[3]


class ConfigurationError(RuntimeError):
    pass


def invalid(name):
    raise ConfigurationError('invalid_configuration:' + name)


def host_name(value):
    value = value.lower()
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', value) or '..' in value:
        invalid('MODEL_ALLOWED_HOSTS')
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return value
    invalid('MODEL_ALLOWED_HOSTS')


@dataclass
class Settings:
    data_dir: Path = ROOT / 'data'
    privacy_operator: str = ''
    privacy_contact: str = ''
    public_api_origin: str = ''
    backup_retention_days: int = 7
    login_global_per_minute: int = 300
    login_client_per_minute: int = 20
    wechat_app_id: str = ''
    wechat_app_secret: str = field(default='', repr=False)
    tenant_cache_size: int = 8
    auth_session_seconds: int = 604800
    model_base_url: str = ''
    model_api_key: str = field(default='', repr=False)
    model_name: str = ''
    scheduler_enabled: bool = True
    job_runner_enabled: bool = True
    job_poll_seconds: float = 2
    job_lease_seconds: float = 90
    job_heartbeat_seconds: float = 20
    job_shutdown_grace_seconds: float = 15
    job_max_attempts: int = 3
    job_catchup_days: int = 7
    local_only: bool = False
    context_pending_days: int = 7
    context_plan_days: int = 7
    context_completed_days: int = 30
    runtime_profile: str = 'development'
    model_allowed_hosts: tuple[str, ...] = ('dashscope.aliyuncs.com',)
    model_egress_proxy: str = ''
    request_max_bytes: int = 65536
    request_body_seconds: float = 10
    model_response_max_bytes: int = 1048576
    model_timeout_seconds: float = 45
    safety_timeout_seconds: float = 8
    user_requests_per_minute: int = 60
    global_requests_per_minute: int = 600
    model_max_concurrent: int = 2
    user_model_calls_per_day: int = 200
    global_model_calls_per_day: int = 5000
    model_max_output_tokens: int = 4096
    runtime_max_executions: int = 2
    execution_seconds: float = 120
    execution_grace_seconds: float = 15
    obs_enabled: bool = True
    obs_log_level: str = 'INFO'
    obs_metrics_enabled: bool = False
    obs_quality_export_enabled: bool = False
    obs_thread_link_enabled: bool = False
    obs_thread_link_key_file: str = field(default='', repr=False)
    obs_thread_link_key_version: str = ''
    obs_thread_link_key: bytes = field(default=b'', init=False, repr=False)

    def __post_init__(self):
        self.data_dir = Path(self.data_dir)
        if type(self.auth_session_seconds) is not int or not 60 <= self.auth_session_seconds <= 2592000:
            invalid('AUTH_SESSION_SECONDS')
        for name in ('obs_enabled', 'obs_metrics_enabled', 'obs_quality_export_enabled', 'obs_thread_link_enabled'):
            if type(getattr(self, name)) is not bool:
                invalid(name.upper())
        if self.obs_log_level not in ('INFO', 'WARNING'):
            invalid('OBS_LOG_LEVEL')
        if (self.obs_quality_export_enabled and not self.obs_metrics_enabled
                or self.obs_metrics_enabled and not self.obs_enabled):
            invalid('OBS_METRICS_ENABLED')
        if self.obs_thread_link_enabled:
            if not re.fullmatch(r'[a-zA-Z0-9_-]{1,24}', self.obs_thread_link_key_version):
                invalid('OBS_THREAD_LINK_KEY_VERSION')
            try:
                with open(self.obs_thread_link_key_file, 'rb') as handle:
                    key = handle.read(4097)
                if not 32 <= len(key) <= 4096 or key == self.model_api_key.encode():
                    invalid('OBS_THREAD_LINK_KEY_FILE')
                self.obs_thread_link_key = key
            except OSError:
                invalid('OBS_THREAD_LINK_KEY_FILE')
        if self.runtime_profile not in ('development', 'hardened'):
            invalid('RUNTIME_PROFILE')
        for name in ('request_max_bytes', 'model_response_max_bytes', 'runtime_max_executions',
                     'backup_retention_days', 'login_global_per_minute', 'login_client_per_minute',
                     'tenant_cache_size', 'user_requests_per_minute', 'global_requests_per_minute', 'model_max_concurrent',
                     'user_model_calls_per_day', 'global_model_calls_per_day', 'model_max_output_tokens',
                     'context_pending_days', 'context_plan_days', 'context_completed_days', 'job_max_attempts', 'job_catchup_days'):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= 100000000:
                invalid(name)
        for name in ('request_body_seconds', 'model_timeout_seconds', 'safety_timeout_seconds',
                     'execution_seconds', 'execution_grace_seconds', 'job_poll_seconds', 'job_lease_seconds',
                     'job_heartbeat_seconds', 'job_shutdown_grace_seconds'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 3600:
                invalid(name)
        if self.job_heartbeat_seconds >= self.job_lease_seconds / 3 or self.job_catchup_days > 7:
            invalid('JOB_LEASE_OR_CATCHUP')
        if type(self.job_runner_enabled) is not bool:
            invalid('JOB_RUNNER_ENABLED')
        if self.runtime_profile == 'hardened':
            if str(self.data_dir).replace('\\', '/') != '/data':
                invalid('DATA_DIR')
            if not self.local_only:
                if not all((self.model_base_url, self.model_name, self.model_api_key)):
                    invalid('MODEL_CONFIGURATION')
                self.validate_model_target()

    def validate_wechat(self):
        # Validate at service startup, before opening any data files. Domain tests
        # and offline evaluations may construct model-only Settings independently.
        if not (self.wechat_app_id.strip() and self.wechat_app_secret.strip()):
            invalid('WECHAT_CONFIGURATION')
        if self.runtime_profile == 'hardened':
            if not self.privacy_operator.strip() or '@' not in self.privacy_contact:
                invalid('PRIVACY_OPERATOR_OR_CONTACT')
            url = urlsplit(self.public_api_origin)
            if url.scheme != 'https' or not url.hostname or '.' not in url.hostname or url.hostname == 'localhost' or url.port not in (None, 443) or url.path not in ('', '/') or url.query or url.fragment or url.username or url.password:
                invalid('PUBLIC_API_ORIGIN')

    def validate_model_target(self):
        try:
            url = urlsplit(self.model_base_url)
            hosts = tuple(host_name(h) for h in self.model_allowed_hosts)
            if (url.scheme != 'https' or url.username or url.password or url.query or url.fragment
                    or url.port not in (None, 443) or not url.hostname
                    or host_name(url.hostname) not in hosts or '%' in url.netloc):
                invalid('MODEL_BASE_URL')
            if self.model_egress_proxy != 'http://egress:3128':
                invalid('MODEL_EGRESS_PROXY')
        except ValueError:
            invalid('MODEL_BASE_URL')

    @classmethod
    def from_env(cls):
        if os.getenv('RUNTIME_PROFILE', 'development') != 'hardened':
            load_dotenv(ROOT / '.env')
        profile = os.getenv('RUNTIME_PROFILE', 'development')
        mode = os.getenv('PROCESSING_MODE', 'model_allowed')
        if mode not in ('model_allowed', 'local_only'):
            invalid('PROCESSING_MODE')
        key, key_file = os.getenv('MODEL_API_KEY', ''), os.getenv('MODEL_API_KEY_FILE', '')
        if key and key_file:
            invalid('MODEL_API_KEY_SOURCE')
        if profile == 'hardened' and key:
            invalid('MODEL_API_KEY_SOURCE')
        if key_file:
            if profile == 'hardened' and key_file != '/run/secrets/model_api_key':
                invalid('MODEL_API_KEY_FILE')
            try:
                with open(key_file, 'rb') as handle:
                    raw = handle.read(4097)
                if len(raw) > 4096:
                    invalid('MODEL_API_KEY_FILE')
                key = raw.decode('utf-8').strip()
                if not key or '\n' in key or '\r' in key:
                    invalid('MODEL_API_KEY_FILE')
            except (OSError, UnicodeError):
                invalid('MODEL_API_KEY_FILE')
        base, model = os.getenv('MODEL_BASE_URL', '').rstrip('/'), os.getenv('MODEL_NAME', '')
        if mode != 'local_only' and any((base, model, key)) and not all((base, model, key)):
            invalid('MODEL_CONFIGURATION')
        if profile == 'hardened' and mode != 'local_only' and not key_file:
            invalid('MODEL_API_KEY_FILE')
        values = dict(data_dir=Path(os.getenv('DATA_DIR') or ('/data' if profile == 'hardened' else ROOT / 'data')),
                      model_base_url=base, model_api_key=key, model_name=model,
                      runtime_profile=profile, local_only=mode == 'local_only',
                      model_egress_proxy=os.getenv('MODEL_EGRESS_PROXY', ''),
                      model_allowed_hosts=tuple(os.getenv('MODEL_ALLOWED_HOSTS', 'dashscope.aliyuncs.com').split(',')))
        secret_file = os.getenv('WECHAT_APP_SECRET_FILE', '')
        secret = ''
        if secret_file:
            try:
                with open(secret_file, 'rb') as handle:
                    raw_secret = handle.read(4097)
                if len(raw_secret) > 4096:
                    invalid('WECHAT_APP_SECRET_FILE')
                secret = raw_secret.decode('utf-8').strip()
                if not secret or len(secret) > 4096 or '\n' in secret or '\r' in secret:
                    invalid('WECHAT_APP_SECRET_FILE')
            except (OSError, UnicodeError):
                invalid('WECHAT_APP_SECRET_FILE')
        for name in ('privacy_operator', 'privacy_contact', 'public_api_origin'):
            values[name] = os.getenv(name.upper(), '')
        values.update(wechat_app_id=os.getenv('WECHAT_APP_ID', ''),
                      wechat_app_secret=secret)
        scheduler = os.getenv('SCHEDULER_ENABLED', 'true').lower()
        if scheduler not in ('true', 'false'):
            invalid('SCHEDULER_ENABLED')
        values['scheduler_enabled'] = scheduler == 'true'
        runner = os.getenv('JOB_RUNNER_ENABLED', 'true').lower()
        if runner not in ('true', 'false'):
            invalid('JOB_RUNNER_ENABLED')
        values['job_runner_enabled'] = runner == 'true'
        for name in ('obs_enabled', 'obs_metrics_enabled', 'obs_quality_export_enabled', 'obs_thread_link_enabled'):
            value = os.getenv(name.upper(), 'true' if name == 'obs_enabled' else 'false').lower()
            if value not in ('true', 'false'):
                invalid(name.upper())
            values[name] = value == 'true'
        for name in ('obs_log_level', 'obs_thread_link_key_file', 'obs_thread_link_key_version'):
            if name.upper() in os.environ:
                values[name] = os.environ[name.upper()]
        for name, typ in (('backup_retention_days', int), ('login_global_per_minute', int), ('login_client_per_minute', int), ('tenant_cache_size', int), ('auth_session_seconds', int), ('context_pending_days', int), ('context_plan_days', int), ('context_completed_days', int),
                          ('user_requests_per_minute', int), ('global_requests_per_minute', int),
                          ('model_max_concurrent', int), ('user_model_calls_per_day', int),
                          ('global_model_calls_per_day', int), ('model_max_output_tokens', int),
                          ('request_max_bytes', int), ('request_body_seconds', float),
                          ('model_response_max_bytes', int), ('model_timeout_seconds', float),
                          ('safety_timeout_seconds', float), ('runtime_max_executions', int),
                          ('execution_seconds', float), ('execution_grace_seconds', float),
                          ('job_poll_seconds', float), ('job_lease_seconds', float), ('job_heartbeat_seconds', float),
                          ('job_shutdown_grace_seconds', float), ('job_max_attempts', int), ('job_catchup_days', int)):
            if name.upper() in os.environ:
                try:
                    values[name] = typ(os.environ[name.upper()])
                except ValueError:
                    invalid(name.upper())
        return cls(**values)

    @property
    def mode(self):
        return 'model' if self.model_base_url and self.model_name and self.model_api_key else 'demo'
