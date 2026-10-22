"""User profile, target and preference contracts."""

from datetime import date
from typing import Literal
import math
import re

from pydantic import Field, field_validator, model_validator

from .base import StrictModel


class Profile(StrictModel):
    age: int = Field(ge=1, le=120)
    sex: Literal['female', 'male', 'other']
    height_cm: float = Field(ge=80, le=250)
    weight_kg: float = Field(ge=20, le=400)
    goal_weight_kg: float = Field(ge=20, le=400)
    goal_date: date
    activity: Literal['sedentary', 'light', 'moderate', 'active']
    nutrition_context: Literal[
        'unspecified', 'general', 'pregnant', 'breastfeeding', 'clinical'
    ] = 'unspecified'


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
    suggestion_fingerprint: str | None = Field(
        default=None, pattern=r'^[a-f0-9]{64}$'
    )

    @model_validator(mode='after')
    def default_reference(self):
        if (
            self.target_source == 'system_default'
            and self.targets is not None
            and not self.suggestion_fingerprint
        ):
            raise ValueError('采用系统默认时需要对应的估算版本，请刷新目标。')
        return self


class Preferences(StrictModel):
    weights: dict[str, float] = Field(default_factory=lambda: {
        'energy': 30,
        'macros': 25,
        'satiety': 20,
        'adherence': 15,
        'snacking': 10,
    })
    meal_times: dict[str, str] = Field(default_factory=lambda: {
        'breakfast': '08:00',
        'lunch': '12:00',
        'dinner': '18:00',
    })
    summary_time: str = '21:00'
    checkin_time: str = '09:00'
    weekly_day: int = Field(default=6, ge=0, le=6)
    notifications_enabled: bool = True

    @field_validator('weights')
    @classmethod
    def valid_weights(cls, values):
        if set(values) != {
            'energy', 'macros', 'satiety', 'adherence', 'snacking'
        }:
            raise ValueError('权重维度不正确')
        if (
            any(
                not math.isfinite(value) or value < 0 or value > 100
                for value in values.values()
            )
            or abs(sum(values.values()) - 100) > .01
        ):
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
