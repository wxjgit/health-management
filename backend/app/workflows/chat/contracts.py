"""定义聊天模块之间交接的追问、命令和回执契约。"""

from typing import Literal

from pydantic import Field

from ...contracts.base import StrictModel


class ClarificationTask(StrictModel):
    task_id: str = Field(min_length=1, max_length=200)
    action_id: str = Field(default='primary', min_length=1, max_length=100)
    event_index: int | None = Field(default=None, ge=0, le=19)
    item_index: int | None = Field(default=None, ge=0, le=39)
    requested_fields: list[str] = Field(default_factory=list, max_length=8)
    question: str = Field(min_length=1, max_length=500)
    required: bool = True
    attempts: int = Field(default=0, ge=0, le=10)
    status: Literal['pending', 'answered', 'skipped', 'cancelled'] = 'pending'


def active_clarification(state):
    """Return the active strict task, rejecting inconsistent checkpoint state."""
    task_id = state.get('active_clarification_id')
    if not task_id:
        return None
    matches = [
        ClarificationTask.model_validate(raw)
        for raw in state.get('clarification_tasks', [])
        if raw.get('task_id') == task_id
    ]
    if len(matches) != 1 or matches[0].status != 'pending':
        raise ValueError('invalid active clarification task')
    return matches[0]
