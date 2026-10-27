"""Public error codes and messages. Exception text is never an API contract."""

from dataclasses import dataclass
from .safety_messages import REPLIES


class AppError(Exception):
    default_code = 'internal_error'

    def __init__(self, *, code=None):
        self.code = code or self.default_code
        spec = ERRORS.get(self.code) if isinstance(self.code, str) else None
        super().__init__(spec.message if spec else '服务处理失败。')


class DomainValidationError(AppError):
    default_code = 'domain_validation_failed'


class ConflictError(AppError):
    default_code = 'resource_version_conflict'


class SafetyPolicyError(DomainValidationError):
    def __init__(self, category):
        if category not in REPLIES:
            raise ValueError('unknown safety category')
        super().__init__(code='safety_' + category)


class WorkflowStateError(ConflictError):
    default_code = 'workflow_state_invalid'


class ModelError(AppError):
    pass


class ModelUnavailableError(ModelError):
    default_code = 'model_unavailable'


class UpstreamModelError(ModelError):
    default_code = 'model_upstream_failed'


class ModelOutputError(UpstreamModelError):
    default_code = 'model_output_rejected'


class PrivacyCleanupPending(AppError):
    default_code = 'privacy_cleanup_pending'


class RuntimeLimitError(AppError):
    default_code = 'runtime_busy'


class ExecutionDeadlineExceeded(RuntimeLimitError):
    default_code = 'execution_deadline_exceeded'


@dataclass(frozen=True)
class ErrorSpec:
    kind: type[AppError]
    status: int
    message: str


ERRORS = {
    'job_lease_lost': ErrorSpec(ConflictError, 409, '后台任务执行权已失效，请刷新状态。'),
    'runtime_busy': ErrorSpec(RuntimeLimitError, 503, '服务正在处理其他请求，请稍后使用原请求重试。'),
    'execution_deadline_exceeded': ErrorSpec(ExecutionDeadlineExceeded, 504, '本次处理已超时；此前已保存的记录保留，请刷新或使用原请求重试。'),
    'request_body_too_large': ErrorSpec(RuntimeLimitError, 413, '请求内容过大，请缩小范围。'),
    'request_body_timeout': ErrorSpec(RuntimeLimitError, 408, '接收请求超时，请重新发送原请求。'),
    'request_encoding_unsupported': ErrorSpec(RuntimeLimitError, 415, '不支持压缩请求内容。'),
    'request_headers_too_large': ErrorSpec(RuntimeLimitError, 431, '请求头超过限制。'),
    'model_response_too_large': ErrorSpec(UpstreamModelError, 502, '模型响应超过大小限制，请缩小范围后重试。'),
    'internal_error': ErrorSpec(AppError, 500, '服务处理失败，请使用同一请求重试或检查本地服务状态。'),
    'privacy_cleanup_pending': ErrorSpec(PrivacyCleanupPending, 503, '相关隐私清理尚未完成，请重试删除任务。'),
    'domain_validation_failed': ErrorSpec(DomainValidationError, 422, '操作参数不合法，请检查后重试。'),
    'policy_scope_denied': ErrorSpec(DomainValidationError, 422, '操作超出当前支持范围，请缩小范围或使用本地查询。'),
    'fact_content_mismatch': ErrorSpec(DomainValidationError, 422, '事实对象、取值或来源必须与本次原文一致，请核对。'),
    'fact_period_invalid': ErrorSpec(DomainValidationError, 422, '事实期限不合法，请核对有效日期。'),
    'fact_restriction_protected': ErrorSpec(DomainValidationError, 422, '原限制仍需保留，请明确编辑或撤回，不能被普通偏好替代。'),
    'fact_confirmation_required': ErrorSpec(DomainValidationError, 422, '长期信息的适用期限或含义待核对，请先在设置中补充。'),
    'fact_conflict_unresolved': ErrorSpec(ConflictError, 409, '长期信息存在未解决的冲突，请先在设置中核对。'),
    'default_targets_unavailable': ErrorSpec(DomainValidationError, 422, '当前资料不满足默认目标估算条件，请先核对基础资料和限制。'),
    'default_targets_mismatch': ErrorSpec(DomainValidationError, 422, '提交值不是当前系统默认；修改过的目标请作为手动目标提交。'),
    'safety_policy_denied': ErrorSpec(DomainValidationError, 422, '当前信息需要进一步核对或专业评估，暂不能执行此操作。'),
    'request_payload_conflict': ErrorSpec(ConflictError, 409, '同一个 request_id 不能用于不同请求，重复请求内容不一致。'),
    'resource_version_conflict': ErrorSpec(ConflictError, 409, '相关记录或资料版本已经变化，请刷新后重新确认。'),
    'workflow_state_invalid': ErrorSpec(WorkflowStateError, 409, '此流程没有待回答问题或已处理，请刷新后重新开始。'),
    'workflow_interrupt_changed': ErrorSpec(WorkflowStateError, 409, '待回答问题已变化，请刷新后重试。'),
    'workflow_execution_conflict': ErrorSpec(ConflictError, 409, '该流程正在处理或进度已变化，请重试原请求或刷新状态。'),
    'plan_confirmation_in_progress': ErrorSpec(ConflictError, 409, '计划正在确认，请稍后刷新状态，尚未取消。'),
    'workflow_expired': ErrorSpec(ConflictError, 409, '此事项已过期，请重新开始；此前已保存的记录保留。'),
    'workflow_context_changed': ErrorSpec(ConflictError, 409, '相关记录或上下文已变化或清理，本次处理已停止。'),
    'model_budget_exceeded': ErrorSpec(WorkflowStateError, 409, '本次流程已达到模型调用预算或重新规划上限，请缩小任务或重新开始。'),
    'model_unavailable': ErrorSpec(ModelUnavailableError, 503, '模型服务暂时不可用，请稍后重试；没有自动切换到演示估算。'),
    'model_rate_limited': ErrorSpec(ModelUnavailableError, 503, '模型服务请求过于频繁，请稍后重试。'),
    'model_service_unavailable': ErrorSpec(ModelUnavailableError, 503, '模型服务暂时不可用，请稍后重试。'),
    'model_upstream_failed': ErrorSpec(UpstreamModelError, 502, '上游模型请求失败，请检查模型配置或稍后重试。'),
    'model_response_invalid': ErrorSpec(UpstreamModelError, 502, '模型结构化输出不符合约定，请重试；没有自动切换到演示估算。'),
    'model_output_rejected': ErrorSpec(UpstreamModelError, 502, '模型输出未通过本地范围或原文证据校验，请重试。'),
}
ERRORS.update({
    'safety_' + category: ErrorSpec(SafetyPolicyError, 422, message)
    for category, message in REPLIES.items()
})


def public_error(error):
    """Reject unknown codes and mismatched exception/code combinations."""
    spec = ERRORS.get(error.code) if isinstance(error, AppError) and isinstance(error.code, str) else None
    if spec is None or not isinstance(error, spec.kind):
        return 'internal_error', ERRORS['internal_error']
    return error.code, spec
