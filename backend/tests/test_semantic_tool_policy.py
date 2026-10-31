"""验证语义工具选择不会突破本地数据范围策略。"""

from types import SimpleNamespace
import pytest
from app.workflows.chat.policy import ChatPlanPolicy
from app.common.errors import ModelOutputError


@pytest.mark.parametrize("text", ["今天是不是吃的有点油腻了", "今天吃得油不油", "感觉今天有点腻"])
def test_model_selects_bounded_view_without_question_keywords(text):
    harness = ChatPlanPolicy(None, SimpleNamespace(mode="model"))
    actions = harness.approve_plan({"input_text": text}, {"actions": [
        {"intent": "advice", "text": text, "data_view": "diet_today"}
    ]})
    assert actions[0].data_view == "diet_today"


def test_query_kind_is_not_overridden_by_food_word_inside_question():
    harness = ChatPlanPolicy(None, SimpleNamespace(mode="model"))
    actions = harness.approve_plan({"input_text": "睡前吃饭那天的运动"}, {"actions": [
        {"intent": "query", "text": "睡前吃饭那天的运动", "query_kind": "exercise"}
    ]})
    assert actions[0].query_kind == "exercise"


def test_model_cannot_attach_writes_to_advice():
    harness = ChatPlanPolicy(None, SimpleNamespace(mode="model"))
    with pytest.raises(ModelOutputError):
        harness.approve_plan({"input_text": "今天吃得油不油"}, {"actions": [
            {"intent": "advice", "text": "模型改写的非原文", "data_view": "diet_today"}
        ]})


def test_unresolved_meal_tool_asks_instead_of_reading_other_meals():
    harness = ChatPlanPolicy(None, SimpleNamespace(mode="model"))
    actions = harness.approve_plan({"input_text": "这餐油不油"}, {"actions": [
        {"intent": "advice", "text": "这餐油不油", "data_view": "meal_reference"}
    ]})
    assert actions[0].intent == "clarify"


def test_explicit_window_can_select_last_week_without_keyword_rejection():
    harness = ChatPlanPolicy(None, SimpleNamespace(mode="model"))
    actions = harness.approve_plan({"input_text": "上周吃得如何"}, {"actions": [
        {"intent": "advice", "text": "上周吃得如何", "data_view": "diet_week",
         "view_start": "2026-10-19", "view_end": "2026-10-25"}
    ]})
    assert str(actions[0].view_end) == "2026-10-25"


def test_oversized_structured_window_is_rejected():
    harness = ChatPlanPolicy(None, SimpleNamespace(mode="model"))
    with pytest.raises(ModelOutputError):
        harness.approve_plan({"input_text": "看一下饮食"}, {"actions": [
            {"intent": "advice", "text": "看一下饮食", "data_view": "diet_week",
             "view_start": "2026-10-01", "view_end": "2026-10-28"}
        ]})
