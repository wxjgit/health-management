"""定义 Chat Graph 独立保存和恢复的流程状态。"""

from typing import Any, TypedDict


class ChatState(TypedDict, total=False):
    """Chat-specific state separated from plan and analysis workflows.

    Runtime-critical channels stay top-level because LangGraph replaces nested
    dictionaries by default. Module boundaries are enforced by the strict
    clarification/operation contracts rather than implicit nested merges.
    """

    thread_id: str
    conversation_id: str
    processing_mode: str

    input_text: str
    source_message_id: str
    source_text: str
    original: str
    safety_input_text: str
    selected_id: str | None
    rounds: int
    incremental_answer: bool

    safety: dict[str, Any]
    safety_context: dict[str, Any]
    safety_fingerprint: str
    safety_classification: dict[str, Any]

    parsed: dict[str, Any]
    extraction: dict[str, Any]
    event_indexes: list[int]
    waiting_indexes: list[int]
    clarification_tasks: list[dict]
    active_clarification_id: str | None
    skip_optional: bool
    abandoned_indexes: list[int]
    unrecorded_count: int
    prepared_events: dict[str, dict]
    conversation_feedback: list[dict]
    feedback_only: bool
    guidance_shown: bool
    guided_topics: list[str]
    processing_plan: list[dict]
    plan_input_text: str
    extra_actions: list[dict]
    parse_context: dict[str, Any]
    correction_candidates: list[dict]
    correction_version: int | None

    clarification_reply: str
    clarification_selection: dict
    reply_plan: dict

    record_ids: list[str]
    saved_indexes: list[int]
    fact_id: str
    tool_schema_version: int
    operation_commands: list[dict]
    operation_results: list[dict]

    reply: str
    answer_disposition: str | None
    cards: list[dict]
    evidence_refs: list[dict]
    response_draft: dict
    process_trace: list[dict]
