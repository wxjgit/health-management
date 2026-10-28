"""Strict, versioned contracts for synthetic end-to-end evaluation cases."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modeling.payloads import ENVELOPES


class EvalModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class EvalTurn(EvalModel):
    input: str = Field(min_length=1, max_length=4000)
    expect_status: Literal['completed', 'waiting_input'] = 'completed'
    expect_safety_category: Literal[
        'general', 'uncertain', 'emergency', 'medical',
        'eating_disorder', 'special_population',
    ] | None = None
    restart_before_turn: bool = False
    replay_count: int = Field(default=0, ge=0, le=3)
    processing_mode: Literal['model_allowed', 'local_only'] | None = None


class ExpectedEvent(EvalModel):
    kind: Literal['meal', 'weight', 'sleep', 'exercise', 'cycle']
    day: str | None = Field(default=None, pattern=r'^20\d{2}-\d{2}-\d{2}$')
    raw_contains: str | None = Field(default=None, min_length=1, max_length=200)
    value: float | None = None
    item: str | None = Field(default=None, min_length=1, max_length=100)
    quantity: float | None = None


class ExpectedState(EvalModel):
    event_count: int | None = Field(default=None, ge=0, le=1000)
    events: list[ExpectedEvent] = Field(default_factory=list, max_length=100)


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


class EvalScenario(EvalModel):
    version: Literal[1] = 1
    id: str = Field(pattern=r'^[a-z0-9][a-z0-9_-]{2,79}$')
    suite: Literal[
        'smoke', 'regression', 'capability', 'safety_adversarial', 'fault_recovery',
    ]
    tags: list[str] = Field(default_factory=list, max_length=30)
    mode: Literal['demo', 'local_only', 'mock', 'provider'] = 'demo'
    conversation_id: str = Field(default='eval', min_length=1, max_length=100)
    initial_state: InitialState = Field(default_factory=InitialState)
    turns: list[EvalTurn] = Field(min_length=1, max_length=20)
    expected_state: ExpectedState = Field(default_factory=ExpectedState)
    expected_calls: ExpectedCalls = Field(default_factory=ExpectedCalls)
    hard_invariants: list[Literal[
        'no_duplicate_business_write',
        'no_unapproved_outbound_field',
        'no_non_general_write',
        'no_local_only_outbound',
    ]] = Field(default_factory=list, max_length=10)
    mock_responses: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)

    @field_validator('tags')
    @classmethod
    def valid_tags(cls, values):
        if len(set(values)) != len(values) or any(
            not isinstance(value, str) or not value or len(value) > 40 for value in values
        ):
            raise ValueError('tags must be unique non-empty short strings')
        return values

    @model_validator(mode='after')
    def valid_mode(self):
        if self.mode == 'mock' and not self.mock_responses:
            raise ValueError('mock scenarios require scripted responses')
        if self.mode != 'mock' and self.mock_responses:
            raise ValueError('scripted responses are only valid in mock mode')
        if any(key not in ENVELOPES or not values
               for key, values in self.mock_responses.items()):
            raise ValueError('mock response purposes must be known and non-empty')
        if self.turns[0].restart_before_turn:
            raise ValueError('first turn cannot restart an absent workflow')
        return self
