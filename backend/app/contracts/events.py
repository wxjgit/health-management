"""Health-event, evidence and nutrition contracts."""

from datetime import date, datetime
from typing import Literal
import hashlib
import math

from pydantic import ConfigDict, Field, model_validator

from .base import StrictModel


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

    field: Literal[
        'occurred_at', 'value', 'exercise_type', 'meal_slot', 'intensity',
        'quality', 'cycle_action', 'item_name', 'item_quantity', 'item_unit',
        'item_size', 'item_cooking', 'item_kcal', 'item_protein_g',
        'item_carbs_g', 'item_fat_g', 'item_nutrition_basis',
    ]
    item_index: int | None = Field(default=None, ge=0, le=39)
    source_message_id: str | None = Field(default=None, min_length=1, max_length=160)
    source_text_hash: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    quote: str | None = Field(default=None, min_length=1, max_length=200)
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)
    source: Literal[
        'unverified', 'user_text', 'user_confirmed', 'model_estimate',
        'derived', 'unverified_legacy', 'existing_record',
    ] = 'unverified'
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
        if self.kind == 'meal' and (
            not self.items or any(item.name is None for item in self.items)
        ):
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
    nutrition_sources: dict[
        Literal['kcal', 'protein_g', 'carbs_g', 'fat_g'],
        Literal['user', 'model_estimate', 'demo_fixture'],
    ] = Field(default_factory=dict)
    nutrition_reference: NutritionReference | None = None
    nutrition_fingerprint: str
    nutrition_source: str
    nutrition_reason: str | None = None
    kcal_range: tuple[float, float] | None = None

    @model_validator(mode='after')
    def valid_nutrition(self):
        if self.kcal_range is not None:
            low, high = self.kcal_range
            if (
                not all(math.isfinite(value) for value in self.kcal_range)
                or low < 0
                or low > high
                or self.kcal is None
                or not low <= self.kcal <= high
            ):
                raise ValueError('热量范围不合理')
        known = {
            key
            for key in ('kcal', 'protein_g', 'carbs_g', 'fat_g')
            if getattr(self, key) is not None
        }
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
