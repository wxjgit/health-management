"""Versioned, bounded safety classification contract. No tools or diagnoses."""
from typing import Literal
from pydantic import Field
from .base import StrictModel

RiskCategory = Literal['emergency', 'medical', 'eating_disorder', 'special_population']


class SafetySource(StrictModel):
    source_id: str = Field(min_length=1, max_length=30)
    text: str = Field(min_length=1, max_length=4000)


class SafetyProfile(StrictModel):
    age: int | None = Field(default=None, ge=1, le=120)
    nutrition_context: Literal['unspecified', 'general', 'pregnant', 'breastfeeding', 'clinical'] = 'unspecified'


class SafetyContext(StrictModel):
    sources: list[SafetySource] = Field(min_length=1, max_length=4)
    profile: SafetyProfile = Field(default_factory=SafetyProfile)


class SafetyEvidence(StrictModel):
    source_id: str = Field(min_length=1, max_length=30)
    quote: str = Field(min_length=1, max_length=300)


class SafetyFinding(StrictModel):
    category: RiskCategory
    subject: Literal['self', 'other', 'unknown']
    assertion: Literal['affirmed', 'denied', 'hypothetical', 'unknown']
    timeframe: Literal['current', 'past', 'unknown']
    evidence: SafetyEvidence


class SafetyClassification(StrictModel):
    findings: list[SafetyFinding] = Field(max_length=8)
    uncertain: bool = Field(strict=True)
