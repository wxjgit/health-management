from datetime import date, datetime
from typing import Literal
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


class MealItem(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    quantity: float | None = Field(default=None, gt=0, le=10000)
    unit: str = Field(default='份', max_length=20)
    size: str | None = Field(default=None, max_length=20)
    cooking: str | None = Field(default=None, max_length=40)
    kcal: float | None = Field(default=None, ge=0, le=10000)
    protein_g: float | None = Field(default=None, ge=0, le=1000)
    carbs_g: float | None = Field(default=None, ge=0, le=2000)
    fat_g: float | None = Field(default=None, ge=0, le=1000)
    source: Literal['user', 'model_estimate', 'unknown'] = 'unknown'
    uncertainty: str = Field(default='待估算', max_length=200)


class EventDraft(StrictModel):
    kind: Literal['meal', 'weight', 'sleep', 'exercise', 'cycle']
    occurred_at: datetime
    raw_text: str = Field(min_length=1, max_length=4000)
    meal_slot: Literal['breakfast', 'lunch', 'dinner', 'snack'] | None = None
    items: list[MealItem] = Field(default_factory=list, max_length=40)
    value: float | None = None
    quality: str | None = Field(default=None, max_length=100)
    exercise_type: str | None = Field(default=None, max_length=100)
    intensity: Literal['low', 'moderate', 'high', 'unknown'] = 'unknown'
    cycle_action: Literal['start', 'end', 'note'] | None = None

    @model_validator(mode='after')
    def valid_event(self):
        if self.occurred_at.tzinfo is None:
            raise ValueError('发生时间必须包含时区')
        if self.kind == 'meal' and not self.items:
            raise ValueError('饮食记录需要至少一个食物')
        if self.kind in ('weight', 'sleep', 'exercise'):
            limits = {'weight': (20, 400), 'sleep': (0, 1440), 'exercise': (0, 1440)}
            lo, hi = limits[self.kind]
            if self.value is None or not math.isfinite(self.value) or not lo <= self.value <= hi:
                raise ValueError(f'{self.kind} 数值缺失或不合理')
        if self.kind == 'cycle' and self.cycle_action is None:
            raise ValueError('生理周期需要开始、结束或备注')
        return self


class ParseResult(StrictModel):
    intent: Literal['record', 'correct', 'query', 'advice', 'fact']
    events: list[EventDraft] = Field(default_factory=list, max_length=20)
    question: str | None = Field(default=None, max_length=300)
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


class ChatRequest(StrictModel):
    request_id: str = Field(min_length=8, max_length=100)
    conversation_id: str = Field(default='main', min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=4000)
    event_id: str | None = None


class ResumeRequest(StrictModel):
    request_id: str = Field(min_length=8, max_length=100)
    interrupt_id: str = Field(min_length=1, max_length=100)
    text: str | None = Field(default=None, max_length=4000)
    approved: bool | None = Field(default=None, strict=True)
    event_id: str | None = None


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

