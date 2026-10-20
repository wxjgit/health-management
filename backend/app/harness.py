"""Local authority for plans, references, data views and outbound model calls.

The model has no repository/tool access. Plans never contain database IDs, SQL,
field selectors or tool loops. Only this module resolves a record reference.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import re
from urllib.parse import urlsplit

from . import clock
from .schemas import (ExtractionResult, PlannedAction, ProcessingPlan, CorrectionResult,
                      EventPatch, AdviceReply, ContextualReply, NutritionEstimates)


REFERENCE = re.compile(r'它|这条|那条|这顿|那顿|上一条|刚才(?:的|那|这)|那个|这个|上面那')
CORRECTION = re.compile(r'不是|改成|修改|纠正|改为|补充.*备注')
DIET = re.compile(r'饮食|吃|喝|餐|食物|分量|份量|咸|油腻|饿|饱|热量|摄入|蛋白|碳水|脂肪')
CONSULT = re.compile(r'建议|推荐|怎么办|怎么|如何|注意|适合|可以|能不能|多少|吗|[？?]|趋势|变化|进展|回顾|复盘|最近|本周|近.*(?:天|周)')
NO_HISTORY = re.compile(r'(?:不要|不许|别|禁止|不想|不用|无需).{0,10}(?:历史|过往|以前的记录|旧记录)')
KINDS = {'weight': r'体重|称重|公斤|千克|kg|斤', 'meal': DIET.pattern,
         'sleep': r'睡|失眠', 'exercise': r'运动|跑步|步行|骑车|健身', 'cycle': r'周期|月经|经期'}
_scope = ContextVar('model_scope', default=None)


class PolicyError(ValueError):
    pass


@dataclass
class CallScope:
    local_only: bool
    account: object
    approved: set[str] = field(default_factory=set)
    grants: set[str] = field(default_factory=set)
    disclosures: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class ApprovedCall:
    purpose: str
    payload: str


# Root fields are a second check, independent of the code that builds a call.
ENVELOPES = {
    'processing_plan': {'task', 'now', 'text', 'interaction', 'schema'},
    'correct_selected': {'task', 'now', 'text', 'record', 'schema'},
    'patch_draft': {'task', 'now', 'text', 'draft', 'requested_fields', 'item_index', 'schema'},
    'personalized_advice': {'task', 'context', 'schema'},
    'contextual_reply': {'task', 'plan', 'schema'},
    'estimate_nutrition': {'task', 'items', 'schema'},
}
OUTPUTS = {
    'processing_plan': ProcessingPlan, 'correct_selected': CorrectionResult,
    'patch_draft': EventPatch, 'personalized_advice': AdviceReply,
    'contextual_reply': ContextualReply, 'estimate_nutrition': NutritionEstimates,
}


class ModelBoundary:
    """An immutable, single-use approval in the current server-created scope."""
    def __init__(self, settings):
        self.settings = settings

    @property
    def local_only(self):
        scope = _scope.get()
        return self.settings.local_only or bool(scope and scope.local_only)

    @property
    def call_count(self):
        scope = _scope.get()
        return len(scope.disclosures) if scope else 0

    @contextmanager
    def request(self, local_only, account):
        scope = CallScope(local_only or self.settings.local_only, account)
        token = _scope.set(scope)
        try:
            yield scope
        finally:
            _scope.reset(token)

    def approve(self, purpose, data):
        scope = _scope.get()
        if scope is None or self.local_only:
            raise PolicyError('当前模式不允许向外部模型发送数据。')
        if purpose not in ENVELOPES or set(data) != ENVELOPES[purpose] or data.get('task') != purpose:
            raise PolicyError('模型请求不符合已批准的数据接口。')
        if data['schema'] != OUTPUTS[purpose].model_json_schema():
            raise PolicyError('模型请求必须使用程序固定的输出结构，不能在Schema中夹带数据。')
        self._check_payload(purpose, data)
        grant = self._grant_key(purpose, self._resource(purpose, data))
        if grant not in scope.grants:
            raise PolicyError('发送内容没有通过本次处理计划的范围检查。')
        scope.grants.remove(grant)
        payload = json.dumps(data, ensure_ascii=False, allow_nan=False)
        if len(payload.encode('utf-8')) > 90000:
            raise PolicyError('本次模型请求超过发送大小限制，请缩小范围。')
        scope.approved.add(purpose + '\n' + payload)
        return ApprovedCall(purpose, payload)

    def grant(self, purpose, resource):
        """Called only by local validated builders, never from a model proposal."""
        scope = _scope.get()
        if scope is None or self.local_only:
            raise PolicyError('当前模式不允许创建外发授权。')
        scope.grants.add(self._grant_key(purpose, resource))

    @staticmethod
    def _grant_key(purpose, resource):
        canonical = json.dumps(resource, ensure_ascii=False, sort_keys=True, allow_nan=False)
        return purpose + ':' + hashlib.sha256(canonical.encode('utf-8')).hexdigest()

    @staticmethod
    def _resource(purpose, data):
        keys = {
            'processing_plan': ('text', 'interaction'),
            'correct_selected': ('text', 'record'),
            'patch_draft': ('text', 'draft', 'requested_fields', 'item_index'),
            'personalized_advice': ('context',), 'contextual_reply': ('plan',),
            'estimate_nutrition': ('items',),
        }[purpose]
        return {key: data[key] for key in keys}

    def consume(self, call):
        scope = _scope.get()
        if not isinstance(call, ApprovedCall) or scope is None or self.local_only:
            raise PolicyError('模型请求没有本次操作的数据授权。')
        key = call.purpose + '\n' + call.payload
        if key not in scope.approved:
            raise PolicyError('模型请求未经批准或授权已经使用。')
        scope.approved.remove(key)
        # The account callback persists counts before dispatch, including failures.
        scope.account(call.purpose, len(call.payload.encode('utf-8')))
        categories = {
            'processing_plan': ['本次输入', '操作状态（不含历史正文）'],
            'correct_selected': ['本次修改要求', '已绑定的单条记录（必要字段）'],
            'patch_draft': ['本次补充', '当前草稿（必要字段）'],
            'personalized_advice': ['当前问题', '已批准的结构化建议上下文'],
            'contextual_reply': ['本次反馈的本地回复要点'],
            'estimate_nutrition': ['本次食物、分量及已知营养'],
        }[call.purpose]
        scope.disclosures.append({'purpose': call.purpose, 'data_categories': categories,
                                  'provider': urlsplit(self.settings.model_base_url).hostname,
                                  'model': self.settings.model_name})
        return json.loads(call.payload)

    @staticmethod
    def _check_payload(purpose, data):
        def forbid(value, keys):
            if isinstance(value, dict):
                if set(value) & keys:
                    raise PolicyError('发送数据包含未获准的历史或内部字段。')
                for item in value.values():
                    forbid(item, keys)
            elif isinstance(value, list):
                for item in value:
                    forbid(item, keys)

        content = {k: v for k, v in data.items() if k != 'schema'}
        forbid(content, {'recent_records', 'messages', 'model_api_key', 'op_key', 'created_at',
                         'correction_id', 'event_id', 'version', 'nutrition_fingerprint'})
        if purpose == 'processing_plan':
            if set(data['interaction']) != {'selected_record', 'previous_result_count'}:
                raise PolicyError('规划阶段只能读取操作状态。')
        if purpose == 'personalized_advice':
            forbid(data['context'], {'raw_text', 'quote', 'recent_dialogue', 'data'})
            validate_advice_payload(data['context'])
        if purpose in ('correct_selected', 'patch_draft'):
            draft = data.get('record', data.get('draft'))
            allowed = {'kind', 'occurred_at', 'raw_text', 'meal_slot', 'items', 'value',
                       'quality', 'exercise_type', 'intensity', 'cycle_action'}
            if set(draft) - allowed:
                raise PolicyError('记录数据包含未批准字段。')
            allowed_item = {'name', 'quantity', 'unit', 'size', 'cooking', 'kcal', 'protein_g',
                            'carbs_g', 'fat_g', 'nutrition_basis', 'source'}
            if any(set(item) - allowed_item for item in draft.get('items', [])):
                raise PolicyError('食物数据包含未批准字段。')
        if purpose == 'estimate_nutrition':
            fields = {'index', 'name', 'quantity', 'unit', 'size', 'cooking', 'requested_nutrients', 'provided_nutrients'}
            if len(data['items']) > 40 or any(set(item) != fields for item in data['items']):
                raise PolicyError('营养估算包含未批准字段或过多食物。')
            nutrients = {'kcal', 'protein_g', 'carbs_g', 'fat_g'}
            for item in data['items']:
                if set(item['requested_nutrients']) - nutrients or set(item['provided_nutrients']) - nutrients:
                    raise PolicyError('营养估算只能使用批准的营养字段。')
        if purpose == 'contextual_reply':
            plan = data['plan']
            if set(plan) - {'stage', 'points', 'feedback', 'boundary', 'context'}:
                raise PolicyError('回复润色包含未批准字段。')
            if any(set(p) - {'point_id', 'topic', 'role', 'text', 'source', 'reviewed_on'} for p in plan['points']):
                raise PolicyError('回复要点包含未批准字段。')
            if any(set(f) - {'topic', 'stance'} for f in plan['feedback']):
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
    allowed = basic | {'active_targets', 'constraints', 'preferences', 'nutrition_context', 'selection_policy'}
    if view in ('diet_today', 'diet_portion'):
        allowed |= {'today', 'recent_feedback', 'resolved_records'}
    if view == 'meal_reference':
        allowed |= {'resolved_records'}
    if view == 'diet_week':
        allowed |= {'recent_days', 'window'}
    if view == 'diet_portion':
        allowed |= {'profile'}
    if view not in ('diet_today', 'diet_week', 'diet_portion', 'meal_reference'):
        raise PolicyError('建议数据视图未开放。')
    fields(context, allowed)
    if 'window' in context:
        fields(context['window'], {'start', 'end'})
    nutrient_keys = {'kcal', 'protein_g', 'carbs_g', 'fat_g'}
    total_keys = nutrient_keys | {'unknown_items', 'complete_nutrition', 'kcal_range'}
    if context.get('active_targets') is not None:
        fields(context['active_targets'], nutrient_keys | {'exercise_minutes'})
    if len(context.get('constraints', [])) > 8 or len(context.get('preferences', [])) > 5:
        raise PolicyError('长期信息超过发送条数上限。')
    if 'profile' in context:
        fields(context['profile'], {'age', 'height_cm', 'weight_kg', 'goal_weight_kg', 'activity', 'nutrition_context'})
    if 'selection_policy' in context:
        fields(context['selection_policy'], {'structured_records_preferred', 'assistant_messages_excluded', 'references_resolved_locally'})
    meals = list(context.get('resolved_records', []))
    if len(meals) > 1:
        raise PolicyError('一次只能解析单条记录指代。')
    if 'today' in context:
        today = context['today']
        fields(today, {'date', 'meals', 'totals', 'remaining', 'targets', 'meal_count', 'record_complete'})
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
            fields(item, {'name', 'quantity', 'unit', 'size', 'cooking', 'nutrients',
                          'nutrition_status', 'nutrition_source', 'nutrition_reason'})
            fields(item['nutrients'], nutrient_keys)
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


def record_projection(event):
    """Never serialize a database row, nutrition metadata or unrelated feedback."""
    data = event.get('data', event)
    keys = ('kind', 'occurred_at', 'raw_text', 'meal_slot', 'value', 'quality',
            'exercise_type', 'intensity', 'cycle_action')
    result = {k: data[k] for k in keys if k in data}
    if 'kind' not in result:
        result['kind'] = event['kind']
    if 'occurred_at' not in result:
        result['occurred_at'] = event['occurred_at']
    items = []
    for item in data.get('items', [])[:40]:
        projected = {k: item[k] for k in ('name', 'quantity', 'unit', 'size', 'cooking') if k in item}
        # Only user-supplied nutrition is needed to interpret an edit.
        sources = item.get('nutrition_sources', {})
        for key in ('kcal', 'protein_g', 'carbs_g', 'fat_g'):
            if sources.get(key) == 'user' or not sources and item.get('source') == 'user':
                projected[key] = item.get(key)
        items.append(projected)
    result['items'] = items
    return result


class PlanHarness:
    def __init__(self, repo, parser):
        self.repo, self.parser = repo, parser

    def previous_ids(self, state):
        return self.repo.previous_reference_ids(state.get('conversation_id', 'main'), state['thread_id'])

    def selected(self, state):
        selected = state.get('selected_id')
        if not selected:
            return None
        record = self.repo.event(selected)
        if record is None:
            raise PolicyError('选中的记录不存在，请重新选择。')
        return record

    def resolve(self, state, action):
        selected = self.selected(state)
        if selected:
            return selected
        # A model claiming "previous" is not sufficient evidence of a reference.
        if REFERENCE.search(state['input_text']) or state['input_text'].startswith('不是'):
            ids = self.previous_ids(state)
            if len(ids) == 1:
                return self.repo.event(ids[0])
        return None

    @staticmethod
    def view_days(text, view):
        limit = {'weight_trend': 28, 'diet_week': 7}.get(view)
        if limit is None:
            return 1
        count = limit
        if re.search(r'上周|上个月|去年|前年', text):
            raise PolicyError('建议暂不自动读取这个历史窗口，请先查询具体日期或改用近期范围。')
        match = re.search(r'(?:最近|过去|近)\s*([\d一二两三四五六七八九十]+)\s*(天|周|星期|个月|月)', text)
        if match:
            raw = match[1]
            numbers = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
                       '六': 6, '七': 7, '八': 8, '九': 9}
            if raw.isdigit():
                count = int(raw)
            elif '十' in raw:
                tens, units = raw.split('十', 1)
                count = numbers.get(tens, 1) * 10 + numbers.get(units, 0)
            else:
                count = numbers.get(raw, limit + 1)
            count *= {'天': 1, '周': 7, '星期': 7, '个月': 30, '月': 30}[match[2]]
        elif '本周' in text:
            count = clock.now().weekday() + 1
        if not 1 <= count <= limit:
            raise PolicyError(f'这个建议视图最多读取近{limit}天，请缩小范围或使用本地查询。')
        return count

    @staticmethod
    def advice_view(text, bound=None):
        if not CONSULT.search(text):
            return 'none'
        if re.search(r'体重|称重|减重|减脂', text) and not DIET.search(text):
            return 'weight_trend'
        if DIET.search(text):
            if re.search(r'分量|份量|吃多少|多大份', text):
                return 'diet_portion'
            if re.search(r'最近|过去|本周|上周|上个月|这几天|近.*(?:天|周)', text):
                return 'diet_week'
            return 'diet_today'
        if bound:
            return {'weight': 'weight_trend', 'meal': 'diet_today'}.get(bound['kind'], 'none')
        return 'none'

    def approve_plan(self, state, plan):
        plan = ProcessingPlan.model_validate(plan)
        actions = []
        if state.get('selected_id') and not any(a.intent in ('correct', 'advice', 'query', 'clarify') for a in plan.actions):
            return [PlannedAction(intent='clarify', text=state['input_text'],
                                  question='你已选中一条历史记录。请明确是修改这条记录，还是退出纠错后新增。')]
        for action in plan.actions:
            if action.text not in state['input_text']:
                raise PolicyError('处理计划必须对应本次输入，不能自行扩展问题。')
            if action.intent != 'record' and action.events:
                raise PolicyError('规划阶段只有新增任务可以携带事件草稿。')
            if action.data_view != 'none' and action.intent != 'advice':
                raise PolicyError('此任务不能申请建议数据。')
            if action.intent == 'advice':
                if NO_HISTORY.search(state['input_text']):
                    if action.data_view != 'none':
                        raise PolicyError('用户已要求不读取历史，不能批准历史数据视图。')
                    actions.append(action)
                    continue
                bound = self.resolve(state, action)
                if REFERENCE.search(action.text) and not bound:
                    return [PlannedAction(intent='clarify', text=action.text,
                                          question='你指的是哪条记录或哪一餐？请在历史页选择，或说明日期和内容。')]
                view = self.advice_view(action.text, bound)
                if view == 'none' and CONSULT.search(action.text):
                    kinds = {k for k, pattern in KINDS.items() if re.search(pattern, state['input_text'], re.I)}
                    if len(kinds) == 1:
                        view = self.advice_view(state['input_text'], bound)
                # A question about one referenced meal does not authorize today's other meals.
                if (bound and bound['kind'] == 'meal' and REFERENCE.search(action.text)
                        and not re.search(r'今天|今日|全天|下一餐|最近|本周', action.text)):
                    if action.data_view not in ('none', 'diet_today', 'diet_portion', 'meal_reference'):
                        raise PolicyError('单餐指代不能扩大为其他历史数据视图。')
                    view = 'meal_reference'
                    action = action.model_copy(update={'data_view': view})
                if action.data_view not in ('none', view):
                    raise PolicyError('申请的数据超出本次问题范围，请明确需要查看的记录类型。')
                self.view_days(state['input_text'], view)
                action = action.model_copy(update={'data_view': view})
            if action.intent == 'fact' and (not action.fact_text or action.fact_text not in action.text):
                raise PolicyError('长期信息必须直接来自本次用户输入。')
            if action.intent == 'query':
                kinds = {k for k, pattern in KINDS.items() if re.search(pattern, action.text, re.I)}
                if action.query_kind and kinds and action.query_kind not in kinds:
                    raise PolicyError('查询类型与本次问题不一致。')
                if len(kinds) == 1:
                    action = action.model_copy(update={'query_kind': next(iter(kinds))})
                if action.query_search and action.query_search not in action.text:
                    raise PolicyError('查询关键词必须来自本次输入。')
                if action.query_start and action.query_end:
                    if not 0 <= (action.query_end - action.query_start).days <= 365:
                        raise PolicyError('查询时间范围应在一年以内。')
            actions.append(action)
        writes = {a.intent for a in actions if a.intent in ('record', 'correct')}
        if len(writes) > 1 or sum(a.intent == 'correct' for a in actions) > 1:
            return [PlannedAction(intent='clarify', text=state['input_text'],
                                  question='这句话同时涉及新增和纠错，或修改多条记录。请先选择要修改的一条记录。')]
        if any(sum(a.intent == intent for a in actions) > 1 for intent in ('query', 'advice', 'fact')):
            raise PolicyError('一次最多执行一个查询、一个建议和一项长期信息保存，请合并范围。')
        return actions

    def prepare(self, state):
        selected = self.selected(state)
        if self.parser.mode == 'model':
            previous_count = len(self.previous_ids(state))
            self.parser.boundary.grant('processing_plan', {
                'text': state['input_text'], 'interaction': {
                    'selected_record': bool(selected), 'previous_result_count': previous_count}})
            plan = self.parser.plan(state['input_text'], bool(selected), previous_count)
        else:
            # Local parsing has no access to global history either.
            prior = self.resolve(state, None) if selected or CORRECTION.search(state['input_text']) else None
            records = [prior] if prior else []
            result = self.parser.demo_parse(state['input_text'], records,
                                            prior['id'] if prior else None, state.get('original'))
            data = result.model_dump(mode='json', exclude={'correction_id'})
            if data['intent'] == 'correct':
                return result, [], []
            plan = ProcessingPlan(actions=[PlannedAction(text=state['input_text'], **data)])
        actions = self.approve_plan(state, plan)
        clarification = next((a for a in actions if a.intent == 'clarify'), None)
        if clarification:
            return ExtractionResult(intent='advice', question=clarification.question or '请说明希望记录、修改或查询的内容。'), [], actions
        correction = next((a for a in actions if a.intent == 'correct'), None)
        if correction:
            prior = self.resolve(state, correction)
            if not prior:
                return ExtractionResult(intent='correct', question='要修改哪条记录？请先选择记录，或明确上一轮记录。'), [], actions
            projection = record_projection(prior)
            self.parser.boundary.grant('correct_selected', {'text': correction.text, 'record': projection})
            updated = self.parser.correct(correction.text, projection)
            if updated.event:
                # Identity and kind are server-bound; relative dates cannot move an old record.
                if updated.event.kind != prior['kind']:
                    raise PolicyError('纠错不能改变记录类型，请另建记录。')
                updated.event.occurred_at = datetime.fromisoformat(prior['occurred_at'])
            result = ExtractionResult(intent='correct', correction_id=prior['id'],
                                      events=[updated.event] if updated.event else [],
                                      question=updated.question or (None if updated.event else '请说明这条记录要改成什么。'))
            return result, [a for a in actions if a is not correction], actions
        records = [a for a in actions if a.intent == 'record']
        if records:
            events = [e for a in records for e in a.events]
            result = ExtractionResult(intent='record', events=events,
                                      feedback=[f for a in records for f in a.feedback])
            if len(records) == 1:
                for key in ('question', 'clarification_event_index', 'clarification_item_index', 'clarification_fields'):
                    setattr(result, key, getattr(records[0], key))
            if not events and not result.question:
                result.question = '你想记录哪项内容？请补充日期、类型和已知信息。'
            return result, [a for a in actions if a.intent != 'record'], actions
        primary = next((a for a in actions if a.intent == 'fact'), actions[0])
        data = primary.model_dump(exclude={'text', 'target', 'data_view'})
        return ExtractionResult.model_validate(data), [a for a in actions if a is not primary], actions
