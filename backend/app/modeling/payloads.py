"""Whitelists and structural validation for outbound model payloads."""

from ..contracts.workflows import (
    AdviceReply,
    ContextualReply,
    CorrectionResult,
    EventPatch,
    ProcessingPlan,
)
from ..contracts.events import NutritionEstimates
from ..contracts.safety import SafetyClassification, SafetyContext
from ..contracts.facts import FactProjection
from .errors import PolicyError
from pydantic import ValidationError
from ..policy.output import advice_plan, feedback_options


ENVELOPES = {
    'safety_classification': {'task', 'context', 'schema'},
    'processing_plan': {'task', 'now', 'text', 'interaction', 'schema'},
    'correct_selected': {'task', 'now', 'text', 'record', 'schema'},
    'patch_draft': {
        'task', 'now', 'text', 'draft', 'requested_fields', 'item_index', 'schema'
    },
    'personalized_advice': {'task', 'context', 'options', 'schema'},
    'contextual_reply': {'task', 'plan', 'options', 'schema'},
    'estimate_nutrition': {'task', 'items', 'schema'},
}

OUTPUTS = {
    'safety_classification': SafetyClassification,
    'processing_plan': ProcessingPlan,
    'correct_selected': CorrectionResult,
    'patch_draft': EventPatch,
    'personalized_advice': AdviceReply,
    'contextual_reply': ContextualReply,
    'estimate_nutrition': NutritionEstimates,
}


def validate_payload(purpose, data):
    def forbid(value, keys):
        if isinstance(value, dict):
            if set(value) & keys:
                raise PolicyError('发送数据包含未获准的历史或内部字段。')
            for item in value.values():
                forbid(item, keys)
        elif isinstance(value, list):
            for item in value:
                forbid(item, keys)

    content = {key: value for key, value in data.items() if key != 'schema'}
    forbid(content, {
        'recent_records',
        'messages',
        'model_api_key',
        'op_key',
        'created_at',
        'correction_id',
        'event_id',
        'version',
        'nutrition_fingerprint',
    })
    if purpose == 'safety_classification':
        SafetyContext.model_validate(data['context'])
    if purpose == 'processing_plan':
        if set(data['interaction']) != {'selected_record'}:
            raise PolicyError('规划阶段只能读取是否已选中记录。')
    if purpose == 'personalized_advice':
        forbid(data['context'], {'raw_text', 'quote', 'recent_dialogue', 'data'})
        validate_advice_payload(data['context'])
        if data['options'] != advice_plan(data['context'])['options']:
            raise PolicyError('建议选项必须由本地策略生成。')
    if purpose in ('correct_selected', 'patch_draft'):
        draft = data.get('record', data.get('draft'))
        allowed = {
            'kind', 'occurred_at', 'raw_text', 'meal_slot', 'items', 'value',
            'quality', 'exercise_type', 'intensity', 'cycle_action',
        }
        if set(draft) - allowed:
            raise PolicyError('记录数据包含未批准字段。')
        allowed_item = {
            'name', 'quantity', 'unit', 'size', 'cooking', 'kcal', 'protein_g',
            'carbs_g', 'fat_g', 'nutrition_basis', 'source',
        }
        if any(set(item) - allowed_item for item in draft.get('items', [])):
            raise PolicyError('食物数据包含未批准字段。')
    if purpose == 'estimate_nutrition':
        fields = {
            'index', 'name', 'quantity', 'unit', 'size', 'cooking',
            'requested_nutrients', 'provided_nutrients',
        }
        if len(data['items']) > 40 or any(
            set(item) != fields for item in data['items']
        ):
            raise PolicyError('营养估算包含未批准字段或过多食物。')
        nutrients = {'kcal', 'protein_g', 'carbs_g', 'fat_g'}
        for item in data['items']:
            if (
                set(item['requested_nutrients']) - nutrients
                or set(item['provided_nutrients']) - nutrients
            ):
                raise PolicyError('营养估算只能使用批准的营养字段。')
    if purpose == 'contextual_reply':
        plan = data['plan']
        if data['options'] != feedback_options(plan):
            raise PolicyError('表达选项必须由本地策略生成。')
        if set(plan) - {'stage', 'points', 'feedback', 'boundary', 'context'}:
            raise PolicyError('回复润色包含未批准字段。')
        if any(
            set(point) - {
                'point_id', 'topic', 'role', 'text', 'source', 'reviewed_on'
            }
            for point in plan['points']
        ):
            raise PolicyError('回复要点包含未批准字段。')
        if any(set(item) - {'topic', 'stance'} for item in plan['feedback']):
            raise PolicyError('回复润色不能读取反馈原文。')
        if set(plan.get('context', {})) - {'foods'}:
            raise PolicyError('回复润色不能读取历史上下文。')


def validate_advice_payload(context):
    """Reject additional fields even if a future builder accidentally adds them."""

    def fields(value, allowed):
        if not isinstance(value, dict) or set(value) - set(allowed):
            raise PolicyError('建议上下文包含未批准字段。')

    basic = {'question', 'scene', 'view', 'now'}
    view = context.get('view')
    if view == 'none':
        fields(context, basic)
        return
    if view == 'weight_trend':
        fields(context, basic | {'weights', 'window'})
        fields(context['window'], {'start', 'end'})
        if len(context['weights']) > 28:
            raise PolicyError('体重趋势超出28天视图。')
        for weight in context['weights']:
            fields(weight, {'date', 'weight_kg'})
        return
    allowed = basic | {
        'active_targets', 'constraints', 'preferences', 'nutrition_context',
        'selection_policy',
    }
    if view in ('diet_today', 'diet_portion'):
        allowed |= {'today', 'recent_feedback', 'resolved_records'}
    if view == 'meal_reference':
        allowed |= {'resolved_records'}
    if view == 'diet_week':
        allowed |= {'recent_days', 'window'}
    if view == 'diet_portion':
        allowed |= {'profile'}
    if view not in (
        'diet_today', 'diet_week', 'diet_portion', 'meal_reference'
    ):
        raise PolicyError('建议数据视图未开放。')
    fields(context, allowed)
    if 'window' in context:
        fields(context['window'], {'start', 'end'})
    nutrient_keys = {'kcal', 'protein_g', 'carbs_g', 'fat_g'}
    total_keys = nutrient_keys | {
        'unknown_items', 'complete_nutrition', 'kcal_range'
    }
    if context.get('active_targets') is not None:
        fields(context['active_targets'], nutrient_keys | {'exercise_minutes'})
    if (
        len(context.get('constraints', [])) > 8
        or len(context.get('preferences', [])) > 5
    ):
        raise PolicyError('长期信息超过发送条数上限。')
    for key in ('constraints', 'preferences'):
        for fact in context.get(key, []):
            try:
                FactProjection.model_validate(fact)
            except ValidationError as error:
                raise PolicyError('事实投影字段不合法。') from error
    if 'profile' in context:
        fields(context['profile'], {
            'age', 'height_cm', 'weight_kg', 'goal_weight_kg', 'activity',
            'nutrition_context',
        })
    if 'selection_policy' in context:
        fields(context['selection_policy'], {
            'structured_records_preferred',
            'assistant_messages_excluded',
            'references_resolved_locally',
        })
    meals = list(context.get('resolved_records', []))
    if len(meals) > 1:
        raise PolicyError('一次只能解析单条记录指代。')
    if 'today' in context:
        today = context['today']
        fields(today, {
            'date', 'meals', 'totals', 'remaining', 'targets', 'meal_count',
            'record_complete',
            'nutrition_origins',
        })
        if set(today.get('nutrition_origins', [])) - {'user', 'model_estimate', 'demo_fixture', 'unknown'}:
            raise PolicyError('汇总营养来源类别未获批准。')
        if len(today['meals']) > 10:
            raise PolicyError('今日餐食超过视图上限。')
        meals.extend(today['meals'])
        fields(today['totals'], total_keys)
        if today.get('remaining') is not None:
            fields(today['remaining'], nutrient_keys)
        if today.get('targets') is not None:
            fields(today['targets'], nutrient_keys | {'exercise_minutes'})
    for meal in meals:
        fields(meal, {'date', 'meal_slot', 'items'})
        if len(meal['items']) > 10:
            raise PolicyError('单餐食物超过视图上限。')
        for item in meal['items']:
            fields(item, {
                'name', 'quantity', 'unit', 'size', 'cooking', 'nutrients',
                'nutrition_status', 'nutrition_source', 'nutrition_reason',
                'nutrition_origins',
            })
            fields(item['nutrients'], nutrient_keys)
            if set(item.get('nutrition_origins', [])) - {'user', 'model_estimate', 'demo_fixture', 'unknown'}:
                raise PolicyError('营养来源类别未获批准。')
    days = context.get('recent_days', [])
    if len(days) > 7:
        raise PolicyError('饮食汇总超出7天视图。')
    for day in days:
        fields(day, {'date', 'meal_count', 'diet_complete', 'totals'})
        fields(day['totals'], total_keys)
    if len(context.get('recent_feedback', [])) > 6:
        raise PolicyError('饮食反馈超过视图上限。')
    for feedback in context.get('recent_feedback', []):
        fields(feedback, {'topic', 'day'})
