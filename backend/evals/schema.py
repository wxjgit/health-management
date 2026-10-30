"""Strict, versioned contracts for synthetic end-to-end evaluation cases."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modeling.payloads import ENVELOPES


MetricStatus = Literal['scored', 'not_applicable', 'not_annotated']
Dataset = Literal[
    'recording_state', 'grounding', 'abstention', 'temporal',
    'safety_boundary', 'recovery', 'fact_freshness',
]
FactType = Literal[
    'food_preference', 'restriction', 'allergy', 'health_condition',
    'professional_advice', 'legacy_text',
]
METRIC_NAMES = (
    'state_correctness',
    'response_grounding',
    'abstention_calibration',
    'temporal_correctness',
    'safety_policy_correctness',
    'execution_reliability',
)
DATASET_PRIMARY_METRIC = {
    'recording_state': 'state_correctness',
    'grounding': 'response_grounding',
    'abstention': 'abstention_calibration',
    'temporal': 'temporal_correctness',
    'safety_boundary': 'safety_policy_correctness',
    'recovery': 'execution_reliability',
}
DATASET_QUESTIONS = {
    'recording_state': '用户表达是否形成正确业务终态',
    'grounding': '输出中的个人事实是否有当前有效依据',
    'abstention': '系统是否在正确时机回答或拒答',
    'temporal': '时间表达和时间冲突是否处理正确',
    'safety_boundary': '风险路由和权限边界是否正确',
    'recovery': '故障、重启和重放后是否保持正确',
    'fact_freshness': '旧值或已删除值是否重新输出',
}


class EvalModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class MetricAnnotations(EvalModel):
    state_correctness: MetricStatus
    response_grounding: MetricStatus
    abstention_calibration: MetricStatus
    temporal_correctness: MetricStatus
    safety_policy_correctness: MetricStatus
    execution_reliability: MetricStatus


class EvalTurn(EvalModel):
    input: str = Field(min_length=1, max_length=4000)
    expect_status: Literal['completed', 'waiting_input'] = 'completed'
    expect_safety_category: Literal[
        'general', 'uncertain', 'emergency', 'medical',
        'eating_disorder', 'special_population',
    ] | None = None
    expect_abstain: bool | None = None
    expect_reply_contains: list[str] = Field(default_factory=list, max_length=20)
    expect_reply_excludes: list[str] = Field(default_factory=list, max_length=20)
    restart_before_turn: bool = False
    replay_count: int = Field(default=0, ge=0, le=3)
    processing_mode: Literal['model_allowed', 'local_only'] | None = None

    @field_validator('expect_reply_contains', 'expect_reply_excludes')
    @classmethod
    def valid_reply_markers(cls, values):
        if len(values) != len(set(values)) or any(
            not isinstance(value, str) or not value.strip() or len(value) > 200
            for value in values
        ):
            raise ValueError('reply markers must be unique non-empty short strings')
        return values

    @model_validator(mode='after')
    def distinct_reply_markers(self):
        if set(self.expect_reply_contains) & set(self.expect_reply_excludes):
            raise ValueError('reply marker cannot be both required and forbidden')
        return self


class ExpectedEvent(EvalModel):
    kind: Literal['meal', 'weight', 'sleep', 'exercise', 'cycle']
    day: str | None = Field(default=None, pattern=r'^20\d{2}-\d{2}-\d{2}$')
    raw_contains: str | None = Field(default=None, min_length=1, max_length=200)
    value: float | None = None
    item: str | None = Field(default=None, min_length=1, max_length=100)
    quantity: float | None = None


class ExpectedFact(EvalModel):
    type: FactType
    subject: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=200)
    lifecycle: str | None = Field(default=None, min_length=1, max_length=40)
    valid_from: str | None = Field(default=None, pattern=r'^20\d{2}-\d{2}-\d{2}$')
    valid_until: str | None = Field(default=None, pattern=r'^20\d{2}-\d{2}-\d{2}$')


class ExpectedState(EvalModel):
    event_count: int | None = Field(default=None, ge=0, le=1000)
    events: list[ExpectedEvent] = Field(default_factory=list, max_length=100)
    effective_facts: list[ExpectedFact] = Field(default_factory=list, max_length=100)
    forbidden_effective_facts: list[ExpectedFact] = Field(default_factory=list, max_length=100)


class ExpectedCalls(EvalModel):
    min_by_purpose: dict[str, int] = Field(default_factory=dict)
    max_by_purpose: dict[str, int] = Field(default_factory=dict)
    forbidden_substrings: list[str] = Field(default_factory=list, max_length=30)

    @field_validator('min_by_purpose', 'max_by_purpose')
    @classmethod
    def valid_counts(cls, values):
        if any(key not in ENVELOPES or type(value) is not int or not 0 <= value <= 100
               for key, value in values.items()):
            raise ValueError('model call limits must be small non-negative integers')
        return values


class InitialState(EvalModel):
    profile: dict[str, Any] | None = None
    events: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    facts: list[dict[str, Any]] = Field(default_factory=list, max_length=100)


class FactSelector(EvalModel):
    type: FactType
    subject: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=200)


class EvalMutation(EvalModel):
    after_turn: int = Field(ge=1, le=20)
    operation: Literal['delete_fact']
    selector: FactSelector


class EvalScenario(EvalModel):
    version: Literal[2] = 2
    id: str = Field(pattern=r'^[a-z0-9][a-z0-9_-]{2,79}$')
    suite: Literal[
        'smoke', 'regression', 'capability', 'safety_adversarial', 'fault_recovery',
    ]
    dataset: Dataset
    reference_time: datetime
    metric_annotations: MetricAnnotations
    ignore: bool = False
    ignore_reason: str | None = Field(default=None, max_length=300)
    tags: list[str] = Field(default_factory=list, max_length=30)
    mode: Literal['demo', 'local_only', 'mock', 'provider'] = 'demo'
    conversation_id: str = Field(default='eval', min_length=1, max_length=100)
    initial_state: InitialState = Field(default_factory=InitialState)
    turns: list[EvalTurn] = Field(min_length=1, max_length=20)
    mutations: list[EvalMutation] = Field(default_factory=list, max_length=20)
    expected_state: ExpectedState = Field(default_factory=ExpectedState)
    expected_calls: ExpectedCalls = Field(default_factory=ExpectedCalls)
    hard_invariants: list[Literal[
        'no_duplicate_business_write',
        'no_unapproved_outbound_field',
        'no_non_general_write',
        'no_local_only_outbound',
        'no_stale_fact_output',
    ]] = Field(default_factory=list, max_length=10)
    mock_responses: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)

    @field_validator('reference_time')
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('reference_time must include a timezone')
        return value

    @field_validator('tags')
    @classmethod
    def valid_tags(cls, values):
        if len(set(values)) != len(values) or any(
            not isinstance(value, str) or not value or len(value) > 40 for value in values
        ):
            raise ValueError('tags must be unique non-empty short strings')
        return values

    @model_validator(mode='after')
    def valid_scenario(self):
        if self.mode == 'mock' and not self.mock_responses:
            raise ValueError('mock scenarios require scripted responses')
        if self.mode != 'mock' and self.mock_responses:
            raise ValueError('scripted responses are only valid in mock mode')
        if any(key not in ENVELOPES or not values
               for key, values in self.mock_responses.items()):
            raise ValueError('mock response purposes must be known and non-empty')
        if self.turns[0].restart_before_turn:
            raise ValueError('first turn cannot restart an absent workflow')
        if any(mutation.after_turn > len(self.turns) for mutation in self.mutations):
            raise ValueError('mutation turn is outside the scenario')
        if self.ignore and not (self.ignore_reason or '').strip():
            raise ValueError('ignored scenarios require ignore_reason')
        if not self.ignore and self.ignore_reason is not None:
            raise ValueError('active scenarios cannot declare ignore_reason')
        annotations = self.metric_annotations.model_dump()
        primary = DATASET_PRIMARY_METRIC.get(self.dataset)
        if primary and annotations[primary] != 'scored':
            raise ValueError('dataset primary metric must be scored')
        if self.dataset == 'fact_freshness' and 'no_stale_fact_output' not in self.hard_invariants:
            raise ValueError('fact freshness scenarios require no_stale_fact_output')
        if annotations['abstention_calibration'] == 'scored' and not any(
            turn.expect_abstain is not None for turn in self.turns
        ):
            raise ValueError('scored abstention metric requires expect_abstain')
        if annotations['temporal_correctness'] == 'scored' and not (
            any(event.day is not None for event in self.expected_state.events)
            or any(
                fact.valid_from is not None or fact.valid_until is not None
                for fact in (
                    self.expected_state.effective_facts
                    + self.expected_state.forbidden_effective_facts
                )
            )
        ):
            raise ValueError('scored temporal metric requires a dated event or fact assertion')
        if annotations['execution_reliability'] == 'scored' and not (
            any(turn.restart_before_turn or turn.replay_count for turn in self.turns)
            or self.suite == 'fault_recovery'
        ):
            raise ValueError('scored reliability metric requires restart, replay or fault recovery')
        return self
