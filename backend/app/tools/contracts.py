"""定义聊天工具的版本化命令、结果、执行上下文和最小数据投影。"""

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from ..contracts.events import PreparedEvent
from ..contracts.facts import FactDraft


TOOL_SCHEMA_VERSION = 1
EventKind = Literal['meal', 'weight', 'sleep', 'exercise', 'cycle']
CommandStatus = Literal['pending', 'completed', 'skipped', 'failed']


class StrictCommandModel(BaseModel):
    """内部可信边界：禁止未知字段和标量类型的隐式转换。"""

    model_config = ConfigDict(extra='forbid', strict=True)


class CommandBase(StrictCommandModel):
    schema_version: Literal[1] = TOOL_SCHEMA_VERSION
    operation_id: str = Field(min_length=1, max_length=200)
    action_id: str = Field(default='primary', min_length=1, max_length=100)
    status: CommandStatus = 'pending'


class SaveEventCommand(CommandBase):
    kind: Literal['save_event'] = 'save_event'
    event_index: int = Field(ge=0, le=19)
    event: PreparedEvent


class CorrectEventCommand(CommandBase):
    kind: Literal['correct_event'] = 'correct_event'
    event_index: int = Field(ge=0, le=19)
    event_id: str = Field(min_length=1, max_length=200)
    expected_version: int = Field(ge=1)
    event: PreparedEvent


class QueryEventsCommand(CommandBase):
    kind: Literal['query_events'] = 'query_events'
    start: str | None = Field(default=None, max_length=10)
    end: str | None = Field(default=None, max_length=10)
    event_kind: EventKind | None = None
    search: str | None = Field(default=None, max_length=100)
    limit: int = Field(default=100, ge=1, le=100)

    @model_validator(mode='after')
    def valid_window(self):
        start = date.fromisoformat(self.start) if self.start else None
        end = date.fromisoformat(self.end) if self.end else None
        if start and end and start > end:
            raise ValueError('查询起止日期不正确')
        return self


class SaveFactCommand(CommandBase):
    kind: Literal['save_fact'] = 'save_fact'
    fact_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=500)
    draft: FactDraft | None = None
    source_message_id: str | None = Field(default=None, min_length=1, max_length=200)
    context_text: str = Field(min_length=1, max_length=4000)
    peer_versions: dict[str, int] = Field(default_factory=dict, max_length=200)


OperationCommand = Annotated[
    SaveEventCommand | CorrectEventCommand | QueryEventsCommand | SaveFactCommand,
    Field(discriminator='kind'),
]
COMMAND_ADAPTER = TypeAdapter(OperationCommand)


class EventSummary(StrictCommandModel):
    id: str = Field(min_length=1, max_length=200)
    kind: EventKind
    day: str = Field(min_length=10, max_length=10)
    raw_text: str = Field(min_length=1, max_length=4000)


class FactReceiptProjection(StrictCommandModel):
    id: str = Field(min_length=1, max_length=200)
    confirmation: Literal['candidate', 'confirmed', 'conflict']
    lifecycle: Literal[
        'active', 'scheduled', 'needs_review', 'superseded', 'retracted', 'expired',
    ]
    valid_from: str | None = None


class ResultBase(StrictCommandModel):
    schema_version: Literal[1] = TOOL_SCHEMA_VERSION
    operation_id: str = Field(min_length=1, max_length=200)
    action_id: str = Field(default='primary', min_length=1, max_length=100)
    status: Literal['completed', 'skipped', 'failed']


class EventMutationResult(ResultBase):
    kind: Literal['save_event', 'correct_event']
    entity_refs: list[str] = Field(default_factory=list, max_length=20)


class QueryEventsResult(ResultBase):
    kind: Literal['query_events'] = 'query_events'
    records: list[EventSummary] = Field(default_factory=list, max_length=100)


class SaveFactResult(ResultBase):
    kind: Literal['save_fact'] = 'save_fact'
    entity_refs: list[str] = Field(default_factory=list, max_length=1)
    record: FactReceiptProjection


OperationResult = Annotated[
    EventMutationResult | QueryEventsResult | SaveFactResult,
    Field(discriminator='kind'),
]
RESULT_ADAPTER = TypeAdapter(OperationResult)


class ToolExecutionContext(StrictCommandModel):
    schema_version: Literal[1] = TOOL_SCHEMA_VERSION
    user_id: str = Field(min_length=36, max_length=36)
    thread_id: str = Field(min_length=1, max_length=200)
    operation_id: str = Field(min_length=1, max_length=200)
    workflow_generation: int = Field(ge=0)
    granted_capability: Literal['full', 'record_only', 'preference_only']
    policy_version: str = Field(min_length=1, max_length=100)


def validate_command(value):
    # Envelope scalars remain strict through StrictCommandModel. Nested domain
    # models deliberately rehydrate ISO dates from checkpoint JSON.
    return COMMAND_ADAPTER.validate_python(value)


def validate_result(value):
    return RESULT_ADAPTER.validate_python(value)
