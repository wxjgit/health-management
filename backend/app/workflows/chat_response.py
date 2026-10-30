"""Chat workflow response assembly and advice context handling."""

from ..health.advice import build_advice_context, context_trace
from ..chat.feedback import wants_advice
from ..common.errors import DomainValidationError, ModelError, ModelOutputError
from ..common.request_context import log_failure
from ..health import dashboard, evidence, meal_assessment, recommendation
from ..health.nutrition import nutrition_summary
from ..chat.replies import express_plan, make_plan, medical_question, record_receipt
from ..contracts.workflows import PlannedAction
from ..health.facts import fact_receipt
from ..policy.output import advice_plan, fallback_advice, validate_advice, render_feedback, default_feedback_selection


class ChatResponseMixin:
    """Read-side chat node and user-facing result composition."""

    def respond(self, state):
        parsed = state['parsed']
        answer_disposition = None
        if state.get('safety', {}).get('capability') == 'record_only' and parsed['intent'] != 'query':
            records = [self.repo.event(event_id) for event_id in state.get('record_ids', [])]
            records = [record for record in records if record]
            return {
                'reply': (record_receipt(records, corrected=parsed['intent'] == 'correct') if records
                          else '本次没有保存记录。') + '\n离线模式仅处理明确的记录和查询，本次未生成评分或建议。',
                'cards': [], 'evidence_refs': [evidence(record) for record in records],
                'answer_disposition': None,
                'reply_plan': {},
            }
        cards = []
        refs = []
        advice_traces = []
        if parsed['intent'] == 'query':
            records = self.repo.events(
                parsed.get('query_start'),
                parsed.get('query_end'),
                parsed.get('query_kind'),
                parsed.get('query_search'),
                limit=100,
            )
            refs = [evidence(event) for event in records]
            reply = (
                '\n'.join(f'{event["day"]} · {event["raw_text"]}' for event in records)
                if records
                else '这个范围没有原始记录。超过一年后的信息请查看月度摘要，无法还原某一餐。'
            )
            answer_disposition = 'answered' if records else 'abstained'
            trace = self.traced(state, '查询本地数据', f'按模型提取的条件检索到 {len(records)} 条记录。')
        elif parsed['intent'] == 'fact':
            reply = fact_receipt(self.repo.fact(state.get('fact_id', state['thread_id'])), parsed['fact_text'])
        elif state.get('record_ids'):
            records = [self.repo.event(event_id) for event_id in state['record_ids']]
            records = [record for record in records if record]
            refs = [evidence(event) for event in records]
            for event in records:
                if event['kind'] == 'meal':
                    cards.append(meal_assessment(
                        self.repo, event, dashboard(self.repo, event['day'])
                    ))
            reply = record_receipt(records, corrected=parsed['intent'] == 'correct')
            summary = nutrition_summary(records)
            if summary:
                reply += '\n' + summary
            if self.parser.mode == 'demo' and cards:
                reply += '\n当前为演示模式，营养数据来自固定示例值，请勿将其当成精确摄入。'
        elif state.get('unrecorded_count'):
            reply = '已结束补充；缺少必需信息或已取消的记录没有保存。'
        else:
            if state.get('conversation_feedback') and not (self.parser.mode == 'model' and parsed['intent'] == 'advice'):
                reply = '没有保存餐食记录。' if state.get('rounds', 0) else ''
            elif medical_question(state['input_text']):
                reply = make_plan([], 'advice', medical=True)['boundary']
            elif parsed['intent'] == 'advice':
                action = next(
                    (
                        PlannedAction.model_validate(item)
                        for item in state.get('processing_plan', [])
                        if item['intent'] == 'advice'
                    ),
                    PlannedAction(intent='advice', text=state['input_text']),
                )
                context, refs = self.advice_context(state, action)
                advice_traces.append({
                    'title': '筛选建议上下文',
                    'detail': context_trace(context),
                    'source': 'local',
                })
                try:
                    reply = (validate_advice(self.parser.answer_advice(context), context)
                             if self.parser.mode == 'model' else fallback_advice(context))
                    advice_traces.append({
                        'title': '生成个性化建议',
                        'detail': '模型根据受限记录生成回答，正文经过独立审核；失败时使用本地备用建议。',
                        'source': 'model' if self.parser.mode == 'model' else 'local',
                    })
                except ModelError:
                    log_failure('model_advice_fallback')
                    reply = fallback_advice(context)
                    if state.get('conversation_feedback'):
                        fallback_plan = make_plan(state['conversation_feedback'], 'advice')
                        reply = render_feedback(default_feedback_selection(fallback_plan), fallback_plan)
                        if state.get('rounds', 0):
                            reply = '没有保存餐食记录。\n' + reply

                    advice_traces.append({
                        'title': '生成个性化建议',
                        'detail': '模型不可用或回复未通过边界检查，使用同一上下文的本地兜底。',
                        'source': 'local',
                    })
                answer_disposition = 'answered'
            else:
                reply, refs = recommendation(self.repo)
                answer_disposition = 'answered'
            if self.parser.mode == 'demo':
                reply += '\n演示解析支持常见食物、体重、睡眠、运动和周期表达；自由复杂输入请接入模型。'

        # Remaining approved tasks share this workflow and its cumulative call budget.
        for index, raw in enumerate(state.get('extra_actions', [])):
            action = PlannedAction.model_validate(raw)
            if action.intent == 'query':
                records = self.repo.events(
                    action.query_start,
                    action.query_end,
                    action.query_kind,
                    action.query_search,
                    limit=100,
                )
                refs.extend(evidence(event) for event in records)
                reply += '\n' + (
                    '\n'.join(f'{event["day"]} · {event["raw_text"]}' for event in records)
                    or '这个范围没有原始记录。'
                )
            elif action.intent == 'advice':
                context, additional_refs = self.advice_context(state, action)
                refs.extend(additional_refs)
                try:
                    answer = validate_advice(self.parser.answer_advice(context), context)
                except ModelError:
                    log_failure('model_advice_fallback')
                    answer = fallback_advice(context)
                reply += '\n' + answer
                advice_traces.append({
                    'title': '筛选建议上下文',
                    'detail': context_trace(context),
                    'source': 'local',
                })
            elif action.intent == 'fact':
                if not action.fact_text or action.fact_text not in state['input_text']:
                    raise ModelOutputError()
                fact = self.repo.save_fact(f'{state["thread_id"]}:fact:{index}', action.fact_text,
                                           draft=action.fact_draft.model_dump(mode='json') if action.fact_draft else None,
                                           context_text=state['input_text'],
                                           source_message_id=state.get('source_message_id'))
                reply += '\n' + fact_receipt(fact, action.fact_text)

        if parsed['intent'] != 'query':
            trace = list(state.get('process_trace', []))
        trace.extend(advice_traces)
        plan = None
        if (parsed['intent'] not in ('query', 'fact') and state.get('conversation_feedback')
                and not (self.parser.mode == 'model' and parsed['intent'] == 'advice')):
            plan = make_plan(
                state['conversation_feedback'],
                'recorded' if state.get('record_ids') else 'advice',
                guided_topics=(
                    [] if wants_advice(state['input_text']) else state.get('guided_topics', [])
                ),
                medical=medical_question((state.get('original') or '') + state['input_text']),
            )
            plan['context'] = {
                'foods': list(dict.fromkeys(
                    item['name']
                    for event in state['extraction']['events']
                    for item in event.get('items', [])
                    if item.get('name')
                ))[:3]
            }
            narrative, response_trace = express_plan(self.parser, plan)
            reply = '\n'.join(part for part in [reply, narrative] if part)
            trace.append(response_trace)
        if state.get('unrecorded_count') and state.get('record_ids'):
            reply += '\n缺少必需信息或已取消的记录没有保存；已完成的独立记录保留。'
        trace.append({
            'title': '生成结果',
            'detail': '由本地业务规则组装固定事实、经校验的表达、卡片和可追溯依据。',
            'source': 'local',
        })
        return {
            'reply': reply,
            'answer_disposition': answer_disposition,
            'cards': cards,
            'evidence_refs': refs,
            'process_trace': trace,
            'reply_plan': plan or {},
        }

    def advice_context(self, state, action):
        # Revalidate saved plans rather than treating a checkpoint as a grant.
        source = state.get('plan_input_text') or state['input_text']
        if action.text not in source:
            # An explicit new consultation during clarification uses the current answer.
            source = state['input_text']
        plan_state = {**state, 'input_text': source}
        approved = self.harness.approve_plan(
            plan_state, {'actions': [action.model_dump()]}
        )
        if approved[0].intent != 'advice':
            raise DomainValidationError(code='policy_scope_denied')
        bound = (
            self.harness.resolve(plan_state, action)
            if approved[0].data_view != 'none'
            else None
        )
        context, refs = build_advice_context(
            self.repo,
            action.text,
            state.get('conversation_id', 'main'),
            state['thread_id'],
            view=approved[0].data_view,
            bound=bound,
            scope_text=source,
            window_start=approved[0].view_start,
            window_end=approved[0].view_end,
        )
        if self.parser.mode == 'model':
            self.parser.boundary.grant('personalized_advice', {
                'context': context, 'options': advice_plan(context)['options'],
            })
        return context, refs
