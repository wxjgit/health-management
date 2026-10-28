"""Conversation and workflow boundary contracts."""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import Field, PrivateAttr, model_validator

from .base import StrictModel
from .facts import FactDraft
from .events import (
    EvidenceSource,
    EventDraft,
    ExtractionEvent,
    FieldEvidence,
    MealFeedback,
    MealItemDraft,
)


ClarificationField = Literal[
    'occurred_at', 'items', 'value', 'cycle_action', 'quantity',
    'intensity', 'cooking', 'quality',
]


class ExtractionResult(StrictModel):
    intent: Literal['record', 'correct', 'query', 'advice', 'fact']
    events: list[ExtractionEvent] = Field(default_factory=list, max_length=20)
    feedback: list[MealFeedback] = Field(default_factory=list, max_length=8)
    question: str | None = Field(default=None, max_length=300)
    clarification_event_index: int | None = Field(default=None, ge=0, le=19)
    clarification_item_index: int | None = Field(default=None, ge=0, le=39)
    clarification_fields: list[ClarificationField] = Field(default_factory=list, max_length=8)
    correction_id: str | None = None
    fact_text: str | None = Field(default=None, max_length=500)
    fact_draft: FactDraft | None = None
    query_start: date | None = None
    query_end: date | None = None
    query_kind: Literal['meal', 'weight', 'sleep', 'exercise', 'cycle'] | None = None
    query_search: str | None = Field(default=None, max_length=100)

    @model_validator(mode='after')
    def correct_target(self):
        if self.correction_id and self.intent != 'correct':
            raise ValueError('只有纠错意图可以修改旧记录')
        if self.intent == 'correct' and self.events and not self.correction_id:
            self.question = self.question or '要修改哪一条记录？请选择历史记录。'
        if self.query_start and self.query_end and self.query_start > self.query_end:
            raise ValueError('查询起止日期不正确')
        return self


class ParseResult(ExtractionResult):
    events: list[EventDraft] = Field(default_factory=list, max_length=20)


class EventPatch(StrictModel):
    """Only explicitly requested fields are applied to the checkpointed draft."""

    occurred_at: datetime | None = None
    items: list[MealItemDraft] | None = Field(default=None, max_length=40)
    value: float | None = None
    cycle_action: Literal['start', 'end', 'note'] | None = None
    quantity: float | None = Field(default=None, gt=0, le=10000)
    unit: str | None = Field(default=None, min_length=1, max_length=20)
    size: str | None = Field(default=None, max_length=20)
    intensity: Literal['low', 'moderate', 'high', 'unknown'] | None = None
    cooking: str | None = Field(default=None, max_length=40)
    quality: str | None = Field(default=None, max_length=100)
    feedback: list[MealFeedback] = Field(default_factory=list, max_length=8)
    evidence: list[FieldEvidence] = Field(default_factory=list, max_length=120)
    evidence_sources: list[EvidenceSource] = Field(default_factory=list, max_length=4)


class ReplySegment(StrictModel):
    point_id: str = Field(min_length=1, max_length=80)
    variant_id: Literal['default', 'warm']


class ContextualReply(StrictModel):
    segments: list[ReplySegment] = Field(min_length=1, max_length=8)


class AdviceReply(StrictModel):
    point_ids: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(default_factory=list, max_length=2)
    answer: str | None = Field(default=None, min_length=1, max_length=1800)
    approved: bool = False
    _reviewed: bool = PrivateAttr(default=False)


DataView = Literal[
    'none', 'weight_trend', 'diet_today', 'diet_week', 'diet_portion',
    'meal_reference',
]


class PlannedAction(StrictModel):
    """A proposal, not authority to access records or execute a write."""

    intent: Literal['record', 'correct', 'query', 'advice', 'fact', 'clarify']
    text: str = Field(min_length=1, max_length=4000)
    target: Literal['selected', 'previous', 'unresolved'] = 'unresolved'
    data_view: DataView = 'none'
    # Explicit tool window; the server enforces bounded spans.
    view_start: date | None = None
    view_end: date | None = None
    events: list[ExtractionEvent] = Field(default_factory=list, max_length=20)
    feedback: list[MealFeedback] = Field(default_factory=list, max_length=8)
    question: str | None = Field(default=None, max_length=300)
    clarification_event_index: int | None = Field(default=None, ge=0, le=19)
    clarification_item_index: int | None = Field(default=None, ge=0, le=39)
    clarification_fields: list[ClarificationField] = Field(default_factory=list, max_length=8)
    fact_text: str | None = Field(default=None, max_length=500)
    fact_draft: FactDraft | None = None
    query_start: date | None = None
    query_end: date | None = None
    query_kind: Literal['meal', 'weight', 'sleep', 'exercise', 'cycle'] | None = None
    query_search: str | None = Field(default=None, max_length=100)


class ProcessingPlan(StrictModel):
    actions: list[PlannedAction] = Field(min_length=1, max_length=4)


class CorrectionResult(StrictModel):
    event: ExtractionEvent | None = None
    question: str | None = Field(default=None, max_length=300)


class ChatRequest(StrictModel):
    request_id: str = Field(min_length=8, max_length=100)
    conversation_id: str = Field(default='main', min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=4000)
    event_id: str | None = None
    processing_mode: Literal['model_allowed', 'local_only'] = 'model_allowed'


class ResumeRequest(StrictModel):
    request_id: str = Field(min_length=8, max_length=100)
    interrupt_id: str = Field(min_length=1, max_length=100)
    text: str | None = Field(default=None, max_length=4000)
    approved: bool | None = Field(default=None, strict=True)
    event_id: str | None = None
    processing_mode: Literal['model_allowed', 'local_only'] | None = None
