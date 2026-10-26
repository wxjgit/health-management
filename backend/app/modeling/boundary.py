"""Single-use authorization boundary for outbound model calls."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import hashlib
import json
from urllib.parse import urlsplit

from .errors import PolicyError
from .payloads import ENVELOPES, OUTPUTS, validate_payload


_scope = ContextVar('model_scope', default=None)


@dataclass
class CallScope:
    local_only: bool
    account: object
    approved: set[str] = field(default_factory=set)
    grants: set[str] = field(default_factory=set)
    disclosures: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class ApprovedCall:
    purpose: str
    payload: str


class ModelBoundary:
    """An immutable, single-use approval in the current server-created scope."""

    def __init__(self, settings):
        self.settings = settings

    @property
    def local_only(self):
        scope = _scope.get()
        return self.settings.local_only or bool(scope and scope.local_only)

    @property
    def call_count(self):
        scope = _scope.get()
        return len(scope.disclosures) if scope else 0

    @contextmanager
    def request(self, local_only, account):
        scope = CallScope(local_only or self.settings.local_only, account)
        token = _scope.set(scope)
        try:
            yield scope
        finally:
            _scope.reset(token)

    def approve(self, purpose, data):
        scope = _scope.get()
        if scope is None or self.local_only:
            raise PolicyError('当前模式不允许向外部模型发送数据。')
        if (
            purpose not in ENVELOPES
            or set(data) != ENVELOPES[purpose]
            or data.get('task') != purpose
        ):
            raise PolicyError('模型请求不符合已批准的数据接口。')
        if data['schema'] != OUTPUTS[purpose].model_json_schema():
            raise PolicyError('模型请求必须使用程序固定的输出结构，不能在Schema中夹带数据。')
        validate_payload(purpose, data)
        grant = self._grant_key(purpose, self._resource(purpose, data))
        if grant not in scope.grants:
            raise PolicyError('发送内容没有通过本次处理计划的范围检查。')
        scope.grants.remove(grant)
        payload = json.dumps(data, ensure_ascii=False, allow_nan=False)
        if len(payload.encode('utf-8')) > 90000:
            raise PolicyError('本次模型请求超过发送大小限制，请缩小范围。')
        scope.approved.add(purpose + '\n' + payload)
        return ApprovedCall(purpose, payload)

    def grant(self, purpose, resource):
        """Called only by local validated builders, never from a model proposal."""
        scope = _scope.get()
        if scope is None or self.local_only:
            raise PolicyError('当前模式不允许创建外发授权。')
        scope.grants.add(self._grant_key(purpose, resource))

    @staticmethod
    def _grant_key(purpose, resource):
        canonical = json.dumps(
            resource, ensure_ascii=False, sort_keys=True, allow_nan=False
        )
        return purpose + ':' + hashlib.sha256(canonical.encode('utf-8')).hexdigest()

    @staticmethod
    def _resource(purpose, data):
        keys = {
            'safety_classification': ('context',),
            'processing_plan': ('text', 'interaction'),
            'correct_selected': ('text', 'record'),
            'patch_draft': ('text', 'draft', 'requested_fields', 'item_index'),
            'personalized_advice': ('context',),
            'contextual_reply': ('plan',),
            'estimate_nutrition': ('items',),
        }[purpose]
        return {key: data[key] for key in keys}

    def consume(self, call):
        scope = _scope.get()
        if not isinstance(call, ApprovedCall) or scope is None or self.local_only:
            raise PolicyError('模型请求没有本次操作的数据授权。')
        key = call.purpose + '\n' + call.payload
        if key not in scope.approved:
            raise PolicyError('模型请求未经批准或授权已经使用。')
        scope.approved.remove(key)
        # The account callback persists counts before dispatch, including failures.
        scope.account(call.purpose, len(call.payload.encode('utf-8')))
        categories = {
            'safety_classification': ['本次输入与必要的用户前文', '年龄及营养适用状态（不含完整档案）'],
            'processing_plan': ['本次输入', '操作状态（不含历史正文）'],
            'correct_selected': ['本次修改要求', '已绑定的单条记录（必要字段）'],
            'patch_draft': ['本次补充', '当前草稿（必要字段）'],
            'personalized_advice': ['当前问题', '已批准的结构化建议上下文'],
            'contextual_reply': ['本次反馈的本地回复要点'],
            'estimate_nutrition': ['本次食物、分量及已知营养'],
        }[call.purpose]
        scope.disclosures.append({
            'purpose': call.purpose,
            'data_categories': categories,
            'provider': urlsplit(self.settings.model_base_url).hostname,
            'model': self.settings.model_name,
        })
        return json.loads(call.payload)
