"""Pydantic contracts grouped by their owning capability."""

from .base import StrictModel
from .events import (
    EventDraft,
    EvidenceSource,
    ExtractionEvent,
    FieldEvidence,
    MealFeedback,
    MealItem,
    MealItemDraft,
    NormalizationTrace,
    NutritionEstimate,
    NutritionEstimates,
    NutritionReference,
    NutritionValues,
    PreparedEvent,
    PreparedMealItem,
)
from .profile import PlanRequest, Preferences, Profile, Targets
from .workflows import (
    AdviceReply,
    ChatRequest,
    ClarificationField,
    ContextualReply,
    CorrectionResult,
    DataView,
    EventPatch,
    ExtractionResult,
    ParseResult,
    PlannedAction,
    ProcessingPlan,
    ReplySegment,
    ResumeRequest,
)

__all__ = [name for name in globals() if not name.startswith('_')]
