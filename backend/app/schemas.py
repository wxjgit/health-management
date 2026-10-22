from datetime import date, datetime
from typing import Literal
import hashlib
import math
import re

from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Profile(StrictModel):
    age: int = Field(ge=1, le=120)
    sex: Literal['female', 'male', 'other']
    height_cm: float = Field(ge=80, le=250)
    weight_kg: float = Field(ge=20, le=400)
    goal_weight_kg: float = Field(ge=20, le=400)
    goal_date: date
    activity: Literal['sedentary', 'light', 'moderate', 'active']
    nutrition_context: Literal['unspecified', 'general', 'pregnant', 'breastfeeding', 'clinical'] = 'unspecified'


class NutritionValues(StrictModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    kcal: float | None = Field(default=None, ge=0, le=10000)
    protein_g: float | None = Field(default=None, ge=0, le=1000)
    carbs_g: float | None = Field(default=None, ge=0, le=2000)
    fat_g: float | None = Field(default=None, ge=0, le=1000)


class MealItemDraft(NutritionValues):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    quantity: float | None = Field(default=None, gt=0, le=10000)
    unit: str = Field(default='份', max_length=20)
    size: str | None = Field(default=None, max_length=20)
    cooking: str | None = Field(default=None, max_length=40)
    nutrition_basis: Literal['consumed', 'per_100g'] = 'consumed'
    source: Literal['user', 'model_estimate', 'unknown'] = 'unknown'
    uncertainty: str = Field(default='待估算', max_length=200)


class MealItem(MealItemDraft):
    name: str = Field(min_length=1, max_length=100)


class MealFeedback(StrictModel):
    topic: Literal['salty', 'oily', 'overeating', 'hungry']
    quote: str = Field(min_length=1, max_length=300)
    day: date | None = None
    stance: Literal['experience', 'question', 'denial'] = 'experience'
    source: Literal['user_report'] = 'user_report'


class NormalizationTrace(StrictModel):
    input_text: str = Field(min_length=1, max_length=200)
    output_value: float | str
    output_unit: str | None = Field(default=None, max_length=20)
    rule_version: str = Field(min_length=1, max_length=80)


class EvidenceSource(StrictModel):
    """Immutable local snapshot used to re-verify evidence after chat retention expires."""
    message_id: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=4000)
    text_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    received_at: datetime

    @model_validator(mode='after')
    def valid_hash_and_time(self):
        expected = hashlib.sha256(self.text.encode('utf-8')).hexdigest()
        if self.text_hash != expected:
            raise ValueError('证据原文哈希不匹配')
        if self.received_at.tzinfo is None:
            raise ValueError('证据来源时间必须包含时区')
        return self


class FieldEvidence(StrictModel):
    """A model may propose quote/field; only local code may mark it verified."""
    field: Literal['occurred_at', 'value', 'exercise_type', 'meal_slot', 'intensity',
                   'quality', 'cycle_action', 'item_name', 'item_quantity', 'item_unit',
                   'item_size', 'item_cooking', 'item_kcal', 'item_protein_g',
                   'item_carbs_g', 'item_fat_g', 'item_nutrition_basis']
    item_index: int | None = Field(default=None, ge=0, le=39)
    source_message_id: str | None = Field(default=None, min_length=1, max_length=160)
    source_text_hash: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    quote: str | None = Field(default=None, min_length=1, max_length=200)
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)
    source: Literal['unverified', 'user_text', 'user_confirmed', 'model_estimate',
                    'derived', 'unverified_legacy', 'existing_record'] = 'unverified'
    normalization: NormalizationTrace | None = None
    ambiguity: bool = False
    policy_version: str | None = Field(default=None, max_length=80)

    @model_validator(mode='after')
    def valid_verified_span(self):
        if (self.start is None) != (self.end is None):
            raise ValueError('证据字符范围必须同时提供起止位置')
        if self.start is not None and self.start >= self.end:
            raise ValueError('证据字符范围不正确')
        if self.source == 'user_text' and (self.quote is None or self.start is None):
            raise ValueError('用户原文证据必须包含原文片段和字符范围')
        if (self.source_message_id is None) != (self.source_text_hash is None):
            raise ValueError('证据来源编号和哈希必须同时提供')
        if self.field.startswith('item_') and self.item_index is None:
            raise ValueError('食物字段证据必须指定食物索引')
        if not self.field.startswith('item_') and self.item_index is not None:
            raise ValueError('非食物字段证据不能指定食物索引')
        return self


class ExtractionEvent(StrictModel):
    """A conversation draft: required information may still be missing."""
    kind: Literal['meal', 'weight', 'sleep', 'exercise', 'cycle']
    occurred_at: datetime | None = None
    raw_text: str = Field(min_length=1, max_length=4000)
    meal_slot: Literal['breakfast', 'lunch', 'dinner', 'snack'] | None = None
    items: list[MealItemDraft] = Field(default_factory=list, max_length=40)
    value: float | None = None
    quality: str | None = Field(default=None, max_length=100)
    exercise_type: str | None = Field(default=None, max_length=100)
    intensity: Literal['low', 'moderate', 'high', 'unknown'] = 'unknown'
    cycle_action: Literal['start', 'end', 'note'] | None = None
    feedback: list[MealFeedback] = Field(default_factory=list, max_length=8)
    evidence: list[FieldEvidence] = Field(default_factory=list, max_length=120)
    evidence_sources: list[EvidenceSource] = Field(default_factory=list, max_length=20)

    @model_validator(mode='after')
    def valid_known_fields(self):
        if self.occurred_at is not None and self.occurred_at.tzinfo is None:
            raise ValueError('发生时间必须包含时区')
        if self.kind in ('weight', 'sleep', 'exercise') and self.value is not None:
            limits = {'weight': (20, 400), 'sleep': (0, 1440), 'exercise': (0, 1440)}
            lo, hi = limits[self.kind]
            if not math.isfinite(self.value) or not lo <= self.value <= hi:
                raise ValueError(f'{self.kind} 数值不合理')
        return self

    def missing_fields(self):
        fields = [] if self.occurred_at is not None else ['occurred_at']
        if self.kind == 'meal' and (not self.items or any(i.name is None for i in self.items)):
            fields.append('items')
        if self.kind in ('weight', 'sleep', 'exercise') and self.value is None:
            fields.append('value')
        if self.kind == 'cycle' and self.cycle_action is None:
            fields.append('cycle_action')
        return fields


class EventDraft(ExtractionEvent):
    """Only complete, validated events may cross the persistence boundary."""
    occurred_at: datetime
    items: list[MealItem] = Field(default_factory=list, max_length=40)

    @model_validator(mode='after')
    def valid_event(self):
        if self.missing_fields():
            raise ValueError('记录缺少必需信息：' + '、'.join(self.missing_fields()))
        return self


class NutritionReference(StrictModel):
    basis: Literal['consumed', 'per_100g']
    values: NutritionValues
    fingerprint: str


class PreparedMealItem(MealItem):
    nutrition_status: Literal['estimated', 'user_provided', 'partial', 'unavailable']
    nutrition_sources: dict[Literal['kcal', 'protein_g', 'carbs_g', 'fat_g'],
                            Literal['user', 'model_estimate', 'demo_fixture']] = Field(default_factory=dict)
    nutrition_reference: NutritionReference | None = None
    nutrition_fingerprint: str
    nutrition_source: str
    nutrition_reason: str | None = None
    kcal_range: tuple[float, float] | None = None

    @model_validator(mode='after')
    def valid_nutrition(self):
        if self.kcal_range is not None:
            low, high = self.kcal_range
            if (not all(math.isfinite(v) for v in self.kcal_range) or low < 0 or low > high
                    or self.kcal is None or not low <= self.kcal <= high):
                raise ValueError('热量范围不合理')
        known = {key for key in ('kcal', 'protein_g', 'carbs_g', 'fat_g') if getattr(self, key) is not None}
        if known != set(self.nutrition_sources):
            raise ValueError('营养数值和来源必须一一对应')
        return self


class PreparedEvent(EventDraft):
    items: list[PreparedMealItem] = Field(default_factory=list, max_length=40)


class NutritionEstimate(NutritionValues):
    index: int = Field(ge=0, le=39)
    uncertainty: str = Field(min_length=1, max_length=200)


class NutritionEstimates(StrictModel):
    items: list[NutritionEstimate] = Field(default_factory=list, max_length=40)


ClarificationField = Literal['occurred_at', 'items', 'value', 'cycle_action', 'quantity',
                             'intensity', 'cooking', 'quality']


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
    text: str = Field(min_length=1, max_length=240)


class ContextualReply(StrictModel):
    segments: list[ReplySegment] = Field(min_length=1, max_length=8)


class AdviceReply(StrictModel):
    answer: str = Field(min_length=1, max_length=1200)


DataView = Literal['none', 'weight_trend', 'diet_today', 'diet_week', 'diet_portion', 'meal_reference']


class PlannedAction(StrictModel):
    """A proposal, not authority to access records or execute a write."""
    intent: Literal['record', 'correct', 'query', 'advice', 'fact', 'clarify']
    text: str = Field(min_length=1, max_length=4000)
    target: Literal['selected', 'previous', 'unresolved'] = 'unresolved'
    data_view: DataView = 'none'
    events: list[ExtractionEvent] = Field(default_factory=list, max_length=20)
    feedback: list[MealFeedback] = Field(default_factory=list, max_length=8)
    question: str | None = Field(default=None, max_length=300)
    clarification_event_index: int | None = Field(default=None, ge=0, le=19)
    clarification_item_index: int | None = Field(default=None, ge=0, le=39)
    clarification_fields: list[ClarificationField] = Field(default_factory=list, max_length=8)
    fact_text: str | None = Field(default=None, max_length=500)
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


class Targets(StrictModel):
    kcal: float = Field(gt=0, le=10000)
    protein_g: float = Field(ge=0, le=1000)
    carbs_g: float = Field(ge=0, le=1500)
    fat_g: float = Field(ge=0, le=1000)
    exercise_minutes: int = Field(default=120, ge=0, le=2000)

    @model_validator(mode='after')
    def coherent(self):
        macro_kcal = self.protein_g * 4 + self.carbs_g * 4 + self.fat_g * 9
        if abs(macro_kcal - self.kcal) > self.kcal * .15:
            raise ValueError('三大营养素折算热量与总热量相差超过 15%，请检查目标')
        return self


class PlanRequest(StrictModel):
    request_id: str = Field(min_length=8, max_length=100)
    targets: Targets | None = None
    note: str = Field(default='', max_length=500)
    target_source: Literal['manual', 'system_default'] = 'manual'
    suggestion_fingerprint: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')

    @model_validator(mode='after')
    def default_reference(self):
        if self.target_source == 'system_default' and self.targets is not None and not self.suggestion_fingerprint:
            raise ValueError('采用系统默认时需要对应的估算版本，请刷新目标。')
        return self


class Preferences(StrictModel):
    weights: dict[str, float] = Field(default_factory=lambda: {
        'energy': 30, 'macros': 25, 'satiety': 20, 'adherence': 15, 'snacking': 10,
    })
    meal_times: dict[str, str] = Field(default_factory=lambda: {
        'breakfast': '08:00', 'lunch': '12:00', 'dinner': '18:00',
    })
    summary_time: str = '21:00'
    checkin_time: str = '09:00'
    weekly_day: int = Field(default=6, ge=0, le=6)
    notifications_enabled: bool = True

    @field_validator('weights')
    @classmethod
    def valid_weights(cls, values):
        if set(values) != {'energy', 'macros', 'satiety', 'adherence', 'snacking'}:
            raise ValueError('权重维度不正确')
        if any(not math.isfinite(v) or v < 0 or v > 100 for v in values.values()) or abs(sum(values.values()) - 100) > .01:
            raise ValueError('权重必须非负，且总和为 100')
        return values

    @field_validator('summary_time', 'checkin_time')
    @classmethod
    def valid_time(cls, value):
        if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
            raise ValueError('时间格式应为 HH:mm')
        return value

    @field_validator('meal_times')
    @classmethod
    def valid_meals(cls, value):
        if set(value) != {'breakfast', 'lunch', 'dinner'}:
            raise ValueError('需要设置三餐时间')
        for time in value.values():
            cls.valid_time(time)
        return value

