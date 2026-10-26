"""Explicit user facts; confirmation is not independent medical verification."""
from datetime import date
from typing import Literal

from pydantic import Field, model_validator

from .base import StrictModel


class FactInput(StrictModel):
    content: str = Field(min_length=1, max_length=500)
    type: Literal['food_preference', 'restriction', 'allergy', 'professional_advice', 'legacy_text'] = 'legacy_text'
    subject: str = Field(default='', max_length=100)
    value: str = Field(default='', max_length=200)
    source_kind: Literal['user_explicit', 'user_reported_professional'] = 'user_explicit'
    valid_from: date | None = None
    valid_until: date | None = None
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
        return self


class FactUpdate(FactInput):
    expected_version: int = Field(ge=1)


class FactDecision(StrictModel):
    expected_version: int = Field(ge=1)
    action: Literal['confirm', 'retract']
