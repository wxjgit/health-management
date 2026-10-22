from typing import Literal

from pydantic import Field

from ..contracts.base import StrictModel


class CompleteRequest(StrictModel):
    complete: bool = Field(strict=True)


class FeedbackRequest(StrictModel):
    feedback: Literal['accepted', 'rejected']


class ReviewRequest(StrictModel):
    request_id: str = Field(min_length=8, max_length=100)
    period: Literal['day', 'week'] = 'week'
