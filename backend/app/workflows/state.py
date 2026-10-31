"""States for the non-chat workflow graphs."""

from typing import Any, TypedDict


class WorkflowState(TypedDict, total=False):
    thread_id: str
    conversation_id: str
    processing_mode: str
    input_text: str
    original: str
    safety_input_text: str
    safety: dict[str, Any]
    safety_context: dict[str, Any]
    safety_fingerprint: str
    safety_classification: dict[str, Any]
    process_trace: list[dict]
    reply: str
    cards: list[dict]
    evidence_refs: list[dict]


class PlanState(WorkflowState, total=False):
    plan_id: str
    plan: dict
    approved: bool
    schedule_anchor: str


class AnalysisState(WorkflowState, total=False):
    period: str
    period_end: str
    analysis: list[dict]


# Compatibility name for code that accepts either non-chat workflow state.
State = WorkflowState


__all__ = ['AnalysisState', 'PlanState', 'State', 'WorkflowState']
