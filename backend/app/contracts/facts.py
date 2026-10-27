"""Explicit user facts; confirmation is not independent medical verification."""
from datetime import date
from typing import Literal

from pydantic import Field, model_validator

from .base import StrictModel


FactType = Literal['food_preference', 'restriction', 'allergy', 'health_condition', 'professional_advice', 'legacy_text']
TimeScope = Literal['persistent', 'bounded', 'temporary_unknown', 'unknown']


class FactSource(StrictModel):
    professional: str = Field(default='', max_length=100)
    institution: str = Field(default='', max_length=150)
    advised_on: date | None = None
    document: str = Field(default='', max_length=200)


class FactEvidence(StrictModel):
    field: Literal['subject', 'value', 'time', 'source']
    quote: str = Field(min_length=1, max_length=500)


class FactDraft(StrictModel):
    """A model proposal has no authority to select or supersede stored facts."""
    type: FactType = 'legacy_text'
    subject: str = Field(default='', max_length=100)
    subject_kind: Literal['food', 'condition', 'nutrient', 'activity', 'other'] = 'other'
    value: str = Field(default='', max_length=200)
    time_scope: TimeScope = 'unknown'
    valid_from: date | None = None
    valid_until: date | None = None
    assertion: Literal['affirmed', 'denied', 'uncertain', 'hypothetical'] = 'affirmed'
    restriction_reason: Literal['preference', 'allergy', 'health_condition', 'professional_advice', 'other', 'unknown'] = 'unknown'
    source_details: FactSource = Field(default_factory=FactSource)
    reported_severity: Literal['unknown', 'mild', 'moderate', 'severe'] = 'unknown'
    evidence: list[FactEvidence] = Field(default_factory=list, max_length=12)


class FactInput(FactDraft):
    content: str = Field(min_length=1, max_length=500)
    source_kind: Literal['user_explicit', 'user_reported_professional'] = 'user_explicit'
    supersedes: str | None = Field(default=None, max_length=160)

    @model_validator(mode='after')
    def valid_period(self):
        if not self.content.strip():
            raise ValueError('长期信息不能为空')
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise ValueError('有效结束日期不能早于开始日期')
        if self.type != 'legacy_text' and (not self.subject.strip() or not self.value.strip()):
            raise ValueError('结构化事实需要对象和内容')
        if self.type == 'professional_advice':
            self.source_kind = 'user_reported_professional'
        aliases = {'不喜欢': 'dislike', '喜欢': 'like', '不吃': 'avoid', '可以接受': 'accept', '接受': 'accept'}
        if self.type == 'food_preference':
            self.value = aliases.get(self.value, self.value)
            if self.value not in ('dislike', 'like', 'avoid', 'accept'):
                raise ValueError('饮食偏好必须是喜欢、不喜欢、不吃或可以接受')
            self.subject_kind = 'food'
        if self.type == 'health_condition':
            self.subject_kind = 'condition'
        allowed = {'allergy': ('allergic', 'denied', 'uncertain'),
                   'health_condition': ('present', 'past', 'resolved', 'uncertain'),
                   'restriction': ('avoid', 'limit', 'required'),
                   'professional_advice': ('avoid', 'limit', 'required', 'advice')}
        if self.type in allowed and self.value not in allowed[self.type]:
            raise ValueError('此类型的事实取值不合法，请选择明确状态')
        if self.value in ('denied', 'resolved'):
            self.assertion = 'denied'
        if self.value == 'uncertain':
            self.assertion = 'uncertain'
        if self.valid_until:
            if self.time_scope == 'persistent':
                raise ValueError('长期信息不能同时设置临时到期日期')
            self.time_scope = 'bounded'
        elif self.time_scope == 'bounded':
            raise ValueError('明确期限需要到期日期')
        if self.type != 'professional_advice' and any(self.source_details.model_dump().values()):
            raise ValueError('来源详情仅用于转述专业建议')
        if any(e.quote not in self.content for e in self.evidence):
            raise ValueError('事实证据必须来自本次原文')
        return self


class FactUpdate(FactInput):
    expected_version: int = Field(ge=1)


class FactDecision(StrictModel):
    expected_version: int = Field(ge=1)
    action: Literal['confirm', 'retract']
    resolution: Literal['select', 'temporary', 'permanent'] = 'select'
    peer_versions: dict[str, int] | None = Field(default=None, max_length=50)


class FactProjection(StrictModel):
    type: FactType
    subject: str = Field(max_length=100)
    value: str = Field(max_length=200)
    description: str = Field(max_length=500)
    enforcement: Literal['soft', 'avoid', 'clinical_review', 'unknown']
    time_scope: TimeScope
    valid_from: date | None
    valid_until: date | None
    source_kind: Literal['user_explicit', 'user_reported_professional', 'legacy', 'unconfirmed_extraction']
    reported_severity: Literal['unknown', 'mild', 'moderate', 'severe']
    lifecycle: Literal['active', 'needs_review']
