"""Local authority for plans, references and approved data views."""

from datetime import datetime
import re

from ..common import clock
from ..evidence import ground_correction, ground_event
from ..common.errors import ConflictError, DomainValidationError, ModelOutputError
from ..modeling.projections import record_projection
from ..contracts.workflows import ExtractionResult, PlannedAction, ProcessingPlan


REFERENCE = re.compile(r'它|这条|那条|这顿|那顿|上一条|刚才(?:的|那|这)|那个|这个|上面那')
CORRECTION = re.compile(r'不是|改成|修改|纠正|改为|补充.*备注')
DIET = re.compile(r'饮食|吃|喝|餐|食物|分量|份量|咸|油腻|饿|饱|热量|摄入|蛋白|碳水|脂肪')
CONSULT = re.compile(
    r'建议|推荐|怎么办|怎么|如何|注意|适合|可以|能不能|多少|吗|[？?]|趋势|变化|进展|回顾|复盘|最近|本周|近.*(?:天|周)'
)
NO_HISTORY = re.compile(r'(?:不要|不许|别|禁止|不想|不用|无需).{0,10}(?:历史|过往|以前的记录|旧记录)')
KINDS = {
    'weight': r'体重|称重|公斤|千克|kg|斤',
    'meal': DIET.pattern,
    'sleep': r'睡|失眠',
    'exercise': r'运动|跑步|步行|骑车|健身',
    'cycle': r'周期|月经|经期',
}


class PlanHarness:
    def __init__(self, repo, parser):
        self.repo, self.parser = repo, parser

    def previous_ids(self, state):
        return self.repo.previous_reference_ids(
            state.get('conversation_id', 'main'), state['thread_id']
        )

    def selected(self, state):
        selected = state.get('selected_id')
        if not selected:
            return None
        record = self.repo.event(selected)
        if record is None:
            raise ConflictError()
        return record

    def resolve(self, state, action):
        selected = self.selected(state)
        if selected:
            return selected
        # Read prior-result metadata lazily, only to resolve an actual reference.
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
            raise DomainValidationError(code='policy_scope_denied')
        match = re.search(
            r'(?:最近|过去|近)\s*([\d一二两三四五六七八九十]+)\s*(天|周|星期|个月|月)', text
        )
        if match:
            raw = match[1]
            numbers = {
                '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
                '五': 5, '六': 6, '七': 7, '八': 8, '九': 9,
            }
            if raw.isdigit():
                count = int(raw)
            elif '十' in raw:
                tens, units = raw.split('十', 1)
                count = numbers.get(tens, 1) * 10 + numbers.get(units, 0)
            else:
                count = numbers.get(raw, limit + 1)
            count *= {
                '天': 1, '周': 7, '星期': 7, '个月': 30, '月': 30,
            }[match[2]]
        elif '本周' in text:
            count = clock.now().weekday() + 1
        if not 1 <= count <= limit:
            raise DomainValidationError(code='policy_scope_denied')
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
            return {
                'weight': 'weight_trend', 'meal': 'diet_today',
            }.get(bound['kind'], 'none')
        return 'none'

    def approve_plan(self, state, plan):
        plan = ProcessingPlan.model_validate(plan)
        actions = []
        if state.get('selected_id') and not any(
            action.intent in ('correct', 'advice', 'query', 'clarify')
            for action in plan.actions
        ):
            return [PlannedAction(
                intent='clarify',
                text=state['input_text'],
                question='你已选中一条历史记录。请明确是修改这条记录，还是退出纠错后新增。',
            )]
        for action in plan.actions:
            if action.fact_draft is not None and action.intent != 'fact':
                raise ModelOutputError()
            if action.text not in state['input_text']:
                raise ModelOutputError()
            if action.intent != 'record' and action.events:
                raise ModelOutputError()
            if action.data_view != 'none' and action.intent != 'advice':
                raise ModelOutputError()
            if action.intent == 'advice':
                if NO_HISTORY.search(state['input_text']):
                    if action.data_view != 'none':
                        raise ModelOutputError()
                    actions.append(action)
                    continue
                bound = self.resolve(state, action)
                if REFERENCE.search(action.text) and not bound:
                    return [PlannedAction(
                        intent='clarify',
                        text=action.text,
                        question='你指的是哪条记录或哪一餐？请在历史页选择，或说明日期和内容。',
                    )]
                view = self.advice_view(action.text, bound)
                if view == 'none' and CONSULT.search(action.text):
                    kinds = {
                        kind
                        for kind, pattern in KINDS.items()
                        if re.search(pattern, state['input_text'], re.I)
                    }
                    if len(kinds) == 1:
                        view = self.advice_view(state['input_text'], bound)
                # A question about one referenced meal does not authorize today's other meals.
                if (
                    bound
                    and bound['kind'] == 'meal'
                    and REFERENCE.search(action.text)
                    and not re.search(r'今天|今日|全天|下一餐|最近|本周', action.text)
                ):
                    if action.data_view not in (
                        'none', 'diet_today', 'diet_portion', 'meal_reference'
                    ):
                        raise ModelOutputError()
                    view = 'meal_reference'
                    action = action.model_copy(update={'data_view': view})
                if action.data_view not in ('none', view):
                    raise ModelOutputError()
                self.view_days(state['input_text'], view)
                action = action.model_copy(update={'data_view': view})
            if action.intent == 'fact' and (
                not action.fact_text or action.fact_text not in action.text
            ):
                raise ModelOutputError()
            if action.intent == 'query':
                kinds = {
                    kind
                    for kind, pattern in KINDS.items()
                    if re.search(pattern, action.text, re.I)
                }
                if action.query_kind and kinds and action.query_kind not in kinds:
                    raise ModelOutputError()
                if len(kinds) == 1:
                    action = action.model_copy(update={'query_kind': next(iter(kinds))})
                if action.query_search and action.query_search not in action.text:
                    raise ModelOutputError()
                if action.query_start and action.query_end:
                    if not 0 <= (action.query_end - action.query_start).days <= 365:
                        raise ModelOutputError()
            if action.intent == 'record':
                item_names = [
                    item.name
                    for event in action.events
                    for item in event.items
                    if item.name
                ]
                action = action.model_copy(update={
                    'events': [
                        ground_event(
                            event,
                            state.get('source_text') or state['input_text'],
                            state.get('source_message_id'),
                            action.text,
                            item_names,
                        )
                        for event in action.events
                    ],
                })
            actions.append(action)
        writes = {
            action.intent
            for action in actions
            if action.intent in ('record', 'correct')
        }
        if len(writes) > 1 or sum(
            action.intent == 'correct' for action in actions
        ) > 1:
            return [PlannedAction(
                intent='clarify',
                text=state['input_text'],
                question='这句话同时涉及新增和纠错，或修改多条记录。请先选择要修改的一条记录。',
            )]
        if any(
            sum(action.intent == intent for action in actions) > 1
            for intent in ('query', 'advice', 'fact')
        ):
            raise ModelOutputError()
        return actions

    def prepare(self, state):
        selected = self.selected(state)
        if self.parser.mode == 'model':
            self.parser.boundary.grant('processing_plan', {
                'text': state['input_text'],
                'interaction': {'selected_record': bool(selected)},
            })
            plan = self.parser.plan(state['input_text'], bool(selected))
        else:
            prior = (
                self.resolve(state, None)
                if selected or CORRECTION.search(state['input_text'])
                else None
            )
            records = [prior] if prior else []
            result = self.parser.demo_parse(
                state['input_text'],
                records,
                prior['id'] if prior else None,
                state.get('original'),
            )
            data = result.model_dump(mode='json', exclude={'correction_id'})
            if data['intent'] == 'correct':
                if prior and result.events:
                    result.events[0] = ground_correction(
                        result.events[0],
                        prior,
                        state.get('source_text') or state['input_text'],
                        state.get('source_message_id'),
                    )
                return result, [], []
            plan = ProcessingPlan(actions=[
                PlannedAction(text=state['input_text'], **data)
            ])
        actions = self.approve_plan(state, plan)
        clarification = next(
            (action for action in actions if action.intent == 'clarify'), None
        )
        if clarification:
            return ExtractionResult(
                intent='advice',
                question=clarification.question or '请说明希望记录、修改或查询的内容。',
            ), [], actions
        correction = next(
            (action for action in actions if action.intent == 'correct'), None
        )
        if correction:
            prior = self.resolve(state, correction)
            if not prior:
                return ExtractionResult(
                    intent='correct',
                    question='要修改哪条记录？请先选择记录，或明确上一轮记录。',
                ), [], actions
            projection = record_projection(prior)
            self.parser.boundary.grant('correct_selected', {
                'text': correction.text,
                'record': projection,
            })
            updated = self.parser.correct(correction.text, projection)
            if updated.event:
                if updated.event.kind != prior['kind']:
                    raise ModelOutputError()
                updated.event.occurred_at = datetime.fromisoformat(prior['occurred_at'])
                updated.event = ground_correction(
                    updated.event,
                    prior,
                    state.get('source_text') or correction.text,
                    state.get('source_message_id'),
                )
            result = ExtractionResult(
                intent='correct',
                correction_id=prior['id'],
                events=[updated.event] if updated.event else [],
                question=(
                    updated.question
                    or (None if updated.event else '请说明这条记录要改成什么。')
                ),
            )
            return result, [
                action for action in actions if action is not correction
            ], actions
        records = [action for action in actions if action.intent == 'record']
        if records:
            events = [event for action in records for event in action.events]
            result = ExtractionResult(
                intent='record',
                events=events,
                feedback=[
                    item for action in records for item in action.feedback
                ],
            )
            if len(records) == 1:
                for key in (
                    'question',
                    'clarification_event_index',
                    'clarification_item_index',
                    'clarification_fields',
                ):
                    setattr(result, key, getattr(records[0], key))
            if not events and not result.question:
                result.question = '你想记录哪项内容？请补充日期、类型和已知信息。'
            return result, [
                action for action in actions if action.intent != 'record'
            ], actions
        primary = next(
            (action for action in actions if action.intent == 'fact'), actions[0]
        )
        data = primary.model_dump(exclude={'text', 'target', 'data_view'})
        return ExtractionResult.model_validate(data), [
            action for action in actions if action is not primary
        ], actions
