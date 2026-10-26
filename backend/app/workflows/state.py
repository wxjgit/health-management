from typing import Any, TypedDict


class State(TypedDict, total=False):
    """Checkpointed state shared by the current workflow graphs.

    The type remains compatible with existing checkpoints while workflow code is
    split into focused modules. Individual graph-specific states can be narrowed
    in a later migration without changing persisted data in this refactor.
    """

    thread_id: str
    conversation_id: str
    input_text: str
    source_message_id: str
    source_text: str
    original: str
    safety_input_text: str
    safety: dict[str, Any]
    safety_context: dict[str, Any]
    safety_fingerprint: str
    safety_classification: dict[str, Any]
    selected_id: str | None
    parsed: dict[str, Any]
    extraction: dict[str, Any]
    event_indexes: list[int]
    waiting_indexes: list[int]
    pending_event_index: int | None
    pending_fields: list[str]
    pending_required: bool
    pending_item_index: int | None
    incremental_answer: bool
    skip_optional: bool
    abandoned_indexes: list[int]
    unrecorded_count: int
    prepared_events: dict[str, dict]
    conversation_feedback: list[dict]
    feedback_only: bool
    guidance_shown: bool
    guided_topics: list[str]
    clarification_reply: str
    reply_plan: dict
    record_ids: list[str]
    saved_indexes: list[int]
    rounds: int
    reply: str
    cards: list[dict]
    evidence_refs: list[dict]
    plan_id: str
    plan: dict
    approved: bool
    period: str
    analysis: list[dict]
    process_trace: list[dict]
    processing_mode: str
    processing_plan: list[dict]
    plan_input_text: str
    extra_actions: list[dict]
    parse_context: dict[str, Any]
    correction_candidates: list[dict]
    correction_version: int | None
