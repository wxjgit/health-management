import hashlib
import json
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any, TypedDict
from threading import RLock
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command, interrupt

from .clock import now, date_from_text, local_day
from .nutrition import prepare_nutrition, nutrition_summary
from .parser import Parser
from .drafts import check_drafts, merge_patch
from .schemas import EventDraft, PreparedEvent
from .conversation import reconcile_feedback, attach_feedback, wants_advice, explicitly_recording
from .replies import make_plan, express_plan, medical_question, record_receipt
from .repository import Repository, dump
from .services import dashboard, evidence, recommendation, meal_assessment, analyze, review, plan_draft, archive


class State(TypedDict, total=False):
    thread_id: str
    input_text: str
    original: str
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


def fingerprint(value):
    return hashlib.sha256(dump(value).encode()).hexdigest()


class Engine:
    def __init__(self, settings):
        self.settings = settings
        self.repo = Repository(settings.data_dir / 'health.db')
        self.lock = RLock()
        self.cp_conn = sqlite3.connect(settings.data_dir / 'checkpoints.db', check_same_thread=False)
        self.cp_conn.execute('PRAGMA journal_mode=WAL')
        self.checkpointer = SqliteSaver(self.cp_conn)
        self.checkpointer.setup()
        self.parser = Parser(settings)
        self.chat_graph = self.build_chat()
        self.plan_graph = self.build_plan()
        self.analysis_graph = self.build_analysis()

    def close(self):
        self.cp_conn.close()
        self.repo.close()

    def recent(self, selected_id=None):
        records = self.repo.events(limit=100)
        if selected_id:
            event = self.repo.event(selected_id)
            if not event:
                raise ValueError('选中的记录不存在')
            records = [event] + [r for r in records if r['id'] != selected_id]
        return records

    def parse(self, state):
        text = state['input_text']
        abandoned = list(state.get('abandoned_indexes', []))
        skip_optional = state.get('skip_optional', False)
        previous_feedback = state.get('conversation_feedback', [])
        supplied_feedback = []
        feedback_only = state.get('feedback_only', False)
        default_day = date_from_text(state.get('original') or text).date().isoformat()
        if state.get('incremental_answer') and state.get('extraction'):
            extraction = json.loads(json.dumps(state['extraction']))
            index = state['pending_event_index']
            occurred_at = extraction['events'][index].get('occurred_at')
            if occurred_at:
                # Relative dates are anchored once, even if the answer arrives next day.
                default_day = local_day(occurred_at)
            if 'conversation_feedback' not in state:
                previous_feedback = reconcile_feedback([], [], state.get('original') or
                    extraction['events'][index]['raw_text'], default_day)
            cancelled = any(w in text for w in ['先不记', '不记了', '取消记录'])
            unknown = text.strip().strip('，。,.!！') in (
                '不知道', '我不知道', '不清楚', '我不清楚', '忘了', '我忘了', '不记得', '我不记得')
            consulting = (wants_advice(text) and not explicitly_recording(text)
                          and not re.search(r'\d|吃了|喝了', text))
            if consulting or (feedback_only and (cancelled or unknown)):
                # An optional feedback follow-up is not a mandatory logging form.
                extraction.update(intent='advice', events=[])
                feedback_only = False
            elif cancelled or (unknown and state.get('pending_required')):
                abandoned.append(index)
            elif unknown:
                skip_optional = True
            else:
                patch = self.parser.patch(text, extraction['events'][index], state['pending_fields'],
                                          state.get('pending_item_index'))
                supplied_feedback = patch.feedback
                extraction['events'][index] = merge_patch(extraction['events'][index], patch,
                                                         state['pending_fields'], text, state.get('pending_item_index'))
                if feedback_only and extraction['events'][index].get('items'):
                    extraction['intent'] = 'record'
                    feedback_only = False
            extraction.update(question=None, clarification_event_index=None,
                              clarification_item_index=None, clarification_fields=[])
        else:
            parsed = self.parser.parse(text, self.recent(state.get('selected_id')),
                                       state.get('selected_id'), state.get('original'))
            extraction = parsed.model_dump(mode='json')
            supplied_feedback = extraction.get('feedback', [])
            if parsed.correction_id:
                prior = self.repo.event(parsed.correction_id)
                previous_feedback = prior['data'].get('feedback', []) if prior else []
                if prior:
                    default_day = prior['day']
            # A model cannot introduce meal feedback from unrelated history.
            for event in extraction['events']:
                supplied_feedback.extend(event.get('feedback', []))
                event['feedback'] = []
            if any(w in text for w in ['不知道', '不清楚']):
                skip_optional = True
        feedback = reconcile_feedback(previous_feedback, supplied_feedback, text, default_day)
        extraction['feedback'] = feedback
        for event in extraction['events']:
            event['feedback'] = []
        attach_feedback(extraction['events'], feedback)
        incomplete_meals_only = (extraction['events'] and all(
            e['kind'] == 'meal' and not e.get('items') for e in extraction['events']))
        if (not state.get('incremental_answer') and extraction['intent'] in ('record', 'advice')
                and feedback and not explicitly_recording(text)
                and (incomplete_meals_only or not extraction['events'])):
            extraction['intent'] = 'advice'
            extraction['question'] = None
            if wants_advice(text) or medical_question(text) or not any(f['stance'] == 'experience' for f in feedback):
                extraction['events'] = []
                feedback_only = False
            else:
                feedback_only = True
                if not extraction['events']:
                    extraction['events'] = [{'kind': 'meal', 'occurred_at': date_from_text(text).isoformat(),
                                             'raw_text': text, 'items': [], 'feedback': feedback}]
        intent_labels = {'record': '记录健康信息', 'correct': '纠正历史记录', 'query': '查询历史',
                         'advice': '获取建议', 'fact': '保存长期信息'}
        source = 'model' if self.settings.mode == 'model' else 'local'
        return {'extraction': extraction, 'incremental_answer': False,
                'conversation_feedback': feedback, 'feedback_only': feedback_only,
                'abandoned_indexes': list(dict.fromkeys(abandoned)), 'skip_optional': skip_optional,
                'process_trace': [
            {'title': '理解输入', 'detail': f'识别为“{intent_labels[extraction["intent"]]}”，保留已知信息并提取待补字段。',
             'source': source},
        ]}

    def validate_drafts(self, state):
        checked = check_drafts(state['extraction'], state.get('rounds', 0),
                               state.get('skip_optional', False), state.get('abandoned_indexes', []))
        if state.get('feedback_only') and state['extraction'].get('events'):
            checked.update(pending_event_index=0, pending_fields=['items'], pending_required=False,
                           pending_item_index=None)
            checked['parsed']['question'] = '如果想一起复盘，这次主要吃了什么？不知道或不想记录也可以。'
        return {**checked, 'process_trace': self.traced(state, '结构校验',
                '检查草稿缺失信息；完整记录通过字段范围、时区和纠错边界校验后才允许保存。')}

    def prepare_nutrition(self, state):
        parsed = state['parsed']
        prepared = dict(state.get('prepared_events', {}))
        if parsed['intent'] not in ('record', 'correct'):
            return {}
        if parsed['intent'] == 'correct':
            if parsed.get('question'):
                return {}
            if len(parsed['events']) != 1 or not parsed.get('correction_id'):
                raise ValueError('一次纠错必须唯一对应一条完整记录')
        meals = 0
        for index, draft in zip(state['event_indexes'], parsed['events']):
            if index in state.get('saved_indexes', []) or index in state.get('waiting_indexes', []):
                continue
            validated = EventDraft.model_validate(draft).model_dump(mode='json')
            cached = prepared.get(str(index))
            previous = cached
            if previous is None and parsed.get('correction_id'):
                prior = self.repo.event(parsed['correction_id'])
                previous = prior['data'] if prior else None
            prepared[str(index)] = prepare_nutrition(validated, self.settings.mode,
                self.parser.estimate_nutrition, previous=previous, user_text=state['input_text'],
                reuse_unavailable=cached is not None)
            meals += validated['kind'] == 'meal'
        update = {'prepared_events': prepared}
        if meals:
            update['process_trace'] = self.traced(state, '营养准备',
                '按完整食物与实际分量准备营养；保留用户数值，校验估算与来源，无法估算时保留未知原因。')
        return update

    @staticmethod
    def prepared_record(state, index):
        record = state.get('prepared_events', {}).get(str(index))
        if record is None:
            raise ValueError('记录尚未经过营养准备，不能保存')
        return PreparedEvent.model_validate(record).model_dump(mode='json')

    @staticmethod
    def traced(state, title, detail):
        return [*state.get('process_trace', []), {'title': title, 'detail': detail, 'source': 'local'}]

    def save_ready(self, state):
        parsed = state['parsed']
        if parsed['intent'] != 'record' or not parsed.get('question'):
            return {}
        ids = list(state.get('record_ids', []))
        indexes = list(state.get('saved_indexes', []))
        for index, draft in zip(state['event_indexes'], parsed['events']):
            if index not in state.get('waiting_indexes', []) and index not in indexes:
                validated = self.prepared_record(state, index)
                saved = self.repo.save_events([validated], f'{state["thread_id"]}:event:{index}')
                ids.extend(saved)
                indexes.append(index)
        return {'record_ids': ids, 'saved_indexes': indexes}

    def route_chat(self, state):
        parsed = state['parsed']
        if parsed.get('question'):
            return 'prepare_clarification'
        if parsed['intent'] in ['record', 'correct'] and parsed['events']:
            return 'persist'
        if parsed['intent'] == 'fact' and parsed.get('fact_text'):
            return 'fact'
        return 'respond'

    def prepare_clarification(self, state):
        plan = make_plan(state.get('conversation_feedback', []), 'clarify',
                         guided_topics=state.get('guided_topics', []),
                         medical=medical_question(state.get('original') or state['input_text']))
        plan['context'] = {'foods': list(dict.fromkeys(i['name'] for e in state['extraction']['events']
                           for i in e.get('items', []) if i.get('name')))[:3]}
        narrative, trace = express_plan(self.parser, plan)
        reply = '\n'.join(p for p in [narrative, state['parsed']['question']] if p)
        return {'clarification_reply': reply, 'reply_plan': plan,
                'guidance_shown': state.get('guidance_shown', False) or bool(plan['points']),
                'guided_topics': list(dict.fromkeys([*state.get('guided_topics', []),
                                 *(p['topic'] for p in plan['points'] if p['role'] == 'guidance')])),
                'process_trace': [*state.get('process_trace', []), trace] if plan['points']
                                 else state.get('process_trace', [])}

    def clarify(self, state):
        parsed = state['parsed']
        payload = {'kind': 'clarification', 'question': state.get('clarification_reply') or parsed['question'],
                   'prompt': parsed['question'],
                   'saved_records': len(state.get('record_ids', [])),
                   'requested_fields': state.get('pending_fields', []),
                   'optional': not state.get('pending_required', True)}
        if parsed['intent'] == 'correct' and not parsed.get('correction_id'):
            payload['options'] = [{'id': e['id'], 'label': f'{e["day"]} {e["raw_text"]}'} for e in self.recent()[:10]]
        answer = interrupt(payload)
        if not isinstance(answer, dict) or not isinstance(answer.get('text'), str):
            raise ValueError('需要文字补充')
        selected = answer.get('event_id') or parsed.get('correction_id') or state.get('selected_id')
        if state.get('pending_event_index') is not None:
            return {'original': state.get('original') or state['input_text'], 'input_text': answer['text'],
                    'selected_id': selected, 'incremental_answer': True,
                    'rounds': state.get('rounds', 0) + 1}
        if parsed['intent'] == 'correct':
            text = state.get('original') or state['input_text']
            if not answer.get('event_id'):
                text += '，' + answer['text']
            return {'input_text': text, 'selected_id': selected, 'rounds': state.get('rounds', 0) + 1}
        return {'original': state.get('original') or state['input_text'], 'input_text': answer['text'],
                'rounds': state.get('rounds', 0) + 1}

    def persist(self, state):
        parsed = state['parsed']
        if parsed.get('question'):
            raise ValueError('待补充的记录不能直接保存')
        ids = list(state.get('record_ids', []))
        for index, draft in zip(state['event_indexes'], parsed['events']):
            if index not in state.get('saved_indexes', []):
                validated = self.prepared_record(state, index)
                ids.extend(self.repo.save_events([validated],
                                                f'{state["thread_id"]}:event:{index}', parsed.get('correction_id')))
        return {'record_ids': list(dict.fromkeys(ids)),
                'process_trace': self.traced(state, '保存记录', f'已由本地程序写入或更新 {len(set(ids))} 条健康记录。')}

    def fact(self, state):
        self.repo.save_fact(state['thread_id'], state['parsed']['fact_text'])
        return {'process_trace': self.traced(state, '保存长期信息', '已由本地程序保存用户明确表达的偏好或限制。')}

    def respond(self, state):
        parsed = state['parsed']
        cards = []
        refs = []
        if parsed['intent'] == 'query':
            records = self.repo.events(parsed.get('query_start'), parsed.get('query_end'),
                                       parsed.get('query_kind'), parsed.get('query_search'), limit=100)
            refs = [evidence(e) for e in records]
            reply = '\n'.join(f'{e["day"]} · {e["raw_text"]}' for e in records) if records else '这个范围没有原始记录。超过一年后的信息请查看月度摘要，无法还原某一餐。'
            trace = self.traced(state, '查询本地数据', f'按模型提取的条件检索到 {len(records)} 条记录。')
        elif parsed['intent'] == 'fact':
            reply = '已保存为你明确提供的长期信息，后续建议会参考它：' + parsed['fact_text']
        elif state.get('record_ids'):
            records = [self.repo.event(i) for i in state['record_ids']]
            records = [r for r in records if r]
            refs = [evidence(e) for e in records]
            for event in records:
                if event['kind'] == 'meal':
                    cards.append(meal_assessment(self.repo, event, dashboard(self.repo, event['day'])))
            reply = record_receipt(records, corrected=parsed['intent'] == 'correct')
            summary = nutrition_summary(records)
            if summary:
                reply += '\n' + summary
            if self.settings.mode == 'demo' and cards:
                reply += '\n当前为演示模式，营养数据来自固定示例值，请勿将其当成精确摄入。'
        elif state.get('unrecorded_count'):
            reply = '已结束补充；缺少必需信息或已取消的记录没有保存。'
        else:
            if state.get('conversation_feedback'):
                reply = '没有保存餐食记录。' if state.get('rounds', 0) else ''
            elif medical_question(state['input_text']):
                reply = make_plan([], 'advice', medical=True)['boundary']
            else:
                reply, refs = recommendation(self.repo)
            if self.settings.mode == 'demo':
                reply += '\n演示解析支持常见食物、体重、睡眠、运动和周期表达；自由复杂输入请接入模型。'
        if parsed['intent'] != 'query':
            trace = list(state.get('process_trace', []))
        plan = None
        if parsed['intent'] not in ('query', 'fact') and state.get('conversation_feedback'):
            plan = make_plan(state['conversation_feedback'], 'recorded' if state.get('record_ids') else 'advice',
                             guided_topics=[] if wants_advice(state['input_text']) else state.get('guided_topics', []),
                             medical=medical_question((state.get('original') or '') + state['input_text']))
            plan['context'] = {'foods': list(dict.fromkeys(i['name'] for e in state['extraction']['events']
                               for i in e.get('items', []) if i.get('name')))[:3]}
            narrative, response_trace = express_plan(self.parser, plan)
            reply = '\n'.join(p for p in [reply, narrative] if p)
            trace.append(response_trace)
        if state.get('unrecorded_count') and state.get('record_ids'):
            reply += '\n缺少必需信息或已取消的记录没有保存；已完成的独立记录保留。'
        trace.append({'title': '生成结果', 'detail': '由本地业务规则组装固定事实、经校验的表达、卡片和可追溯依据。', 'source': 'local'})
        return {'reply': reply, 'cards': cards, 'evidence_refs': refs, 'process_trace': trace,
                'reply_plan': plan or {}}

    def build_chat(self):
        graph = StateGraph(State)
        for name in ['parse', 'validate_drafts', 'prepare_nutrition', 'save_ready', 'prepare_clarification', 'clarify', 'persist', 'fact', 'respond']:
            graph.add_node(name, getattr(self, name))
        graph.add_edge(START, 'parse')
        graph.add_edge('parse', 'validate_drafts')
        graph.add_edge('validate_drafts', 'prepare_nutrition')
        graph.add_edge('prepare_nutrition', 'save_ready')
        graph.add_conditional_edges('save_ready', self.route_chat, {n: n for n in ['prepare_clarification', 'persist', 'fact', 'respond']})
        graph.add_edge('prepare_clarification', 'clarify')
        graph.add_edge('clarify', 'parse')
        graph.add_edge('persist', 'respond')
        graph.add_edge('fact', 'respond')
        graph.add_edge('respond', END)
        return graph.compile(checkpointer=self.checkpointer)

    def prepare_plan(self, state):
        requested = state.get('plan', {})
        plan = plan_draft(self.repo, requested.get('targets'), state.get('input_text', ''),
                          requested.get('target_source', 'manual'), requested.get('suggestion_fingerprint'))
        plan_id = state.get('plan_id') or state['thread_id']
        self.repo.save_plan(plan_id, state['thread_id'], plan)
        # On replay after an already committed insert, reuse the persisted immutable proposal.
        return {'plan': self.repo.plan(plan_id)['data'], 'plan_id': plan_id}

    def approve_plan(self, state):
        plan = state['plan']
        if not plan['needs_confirmation']:
            return {'approved': True}
        answer = interrupt({'kind': 'plan_confirmation', 'question': '是否采用这份计划？确认前当前目标保持不变。',
                            'plan_id': state['plan_id'], 'targets': plan['targets'], 'changes': plan['changes'],
                            'target_source': plan.get('target_source', 'manual'),
                            'target_basis': (plan.get('target_metadata') or {}).get('basis'),
                            'target_warnings': (plan.get('target_metadata') or {}).get('warnings', [])})
        if not isinstance(answer, dict) or type(answer.get('approved')) is not bool:
            raise ValueError('需要明确接受或拒绝计划')
        return {'approved': answer['approved']}

    def activate_plan(self, state):
        status = self.repo.decide_plan(state['plan_id'], state['approved'])
        replies = {'active': '计划已生效。可在计划页查看下一餐、次日重点和一周安排。',
                   'rejected': '已拒绝这份草案，原计划保持不变。',
                   'stale': '确认期间记录或生效计划发生变化，这份草案已过期。请重新生成，当前目标保持不变。'}
        return {'reply': replies.get(status, '草案已处理。'), 'cards': [], 'evidence_refs': state['plan']['evidence_refs']}

    def build_plan(self):
        graph = StateGraph(State)
        graph.add_node('prepare', self.prepare_plan)
        graph.add_node('approve', self.approve_plan)
        graph.add_node('activate', self.activate_plan)
        graph.add_edge(START, 'prepare')
        graph.add_edge('prepare', 'approve')
        graph.add_edge('approve', 'activate')
        graph.add_edge('activate', END)
        return graph.compile(checkpointer=self.checkpointer)

    def build_analysis(self):
        graph = StateGraph(State)
        graph.add_node('analyze', lambda state: {'analysis': analyze(self.repo)})
        graph.add_node('review', lambda state: {'cards': [{'type': 'review', **review(self.repo, state.get('period', 'week'))}],
                                              'reply': '复盘已更新。规律仅引用足够完整的记录；数据不足时不会制造结论。'})
        graph.add_edge(START, 'analyze')
        graph.add_edge('analyze', 'review')
        graph.add_edge('review', END)
        return graph.compile(checkpointer=self.checkpointer)

    def graph_for(self, kind):
        return {'chat': self.chat_graph, 'plan': self.plan_graph, 'analysis': self.analysis_graph}[kind]

    def format_result(self, thread_id, state):
        pending = state.get('__interrupt__')
        result = {'thread_id': thread_id, 'status': 'waiting_input' if pending else 'completed',
                  'reply': state.get('reply', ''), 'cards': state.get('cards', []),
                  'evidence_refs': state.get('evidence_refs', []),
                  'process_trace': state.get('process_trace', [])}
        if pending:
            result['pending_input'] = {'interrupt_id': pending[0].id, **pending[0].value}
            result['reply'] = pending[0].value['question']
        return result

    def run(self, kind, request_id, request_data, initial, conversation='main'):
        with self.lock:
            started = time.perf_counter()
            fp = fingerprint({'kind': kind, 'data': request_data})
            existing = self.repo.workflow_by_request(request_id)
            if existing:
                if existing['fingerprint'] != fp:
                    raise ValueError('同一个 request_id 不能用于不同请求')
                if existing['status'] in ['completed', 'waiting_input']:
                    return existing['result']
                thread_id = existing['id']
                invocation = None if self.graph_for(kind).get_state({'configurable': {'thread_id': thread_id}}).values else {**initial, 'thread_id': thread_id, 'record_ids': [], 'saved_indexes': [], 'rounds': 0}
            else:
                thread_id = str(uuid4())
                self.repo.register_workflow(thread_id, request_id, fp, kind)
                invocation = {**initial, 'thread_id': thread_id, 'record_ids': [], 'saved_indexes': [], 'rounds': 0}
                if kind == 'chat':
                    self.repo.message(request_id, thread_id, 'user', initial['input_text'], conversation=conversation)
            try:
                state = self.graph_for(kind).invoke(invocation, {'configurable': {'thread_id': thread_id}})
                result = self.format_result(thread_id, state)
                result['duration_ms'] = round((time.perf_counter() - started) * 1000)
                self.repo.workflow_result(thread_id, result)
                self.repo.message(thread_id + ':reply', thread_id, 'assistant', result['reply'], result['cards'],
                                  result['evidence_refs'], conversation, result['process_trace'], result['duration_ms'])
                return result
            except Exception:
                self.repo.workflow_result(thread_id, {'thread_id': thread_id, 'status': 'failed', 'reply': '处理失败，可使用原请求重试。'})
                raise

    def resume(self, thread_id, payload):
        with self.lock:
            started = time.perf_counter()
            fp = fingerprint(payload)
            receipt = self.repo.receipt(payload['request_id'])
            if receipt:
                if receipt['fingerprint'] != fp or receipt['thread_id'] != thread_id:
                    raise ValueError('重复请求内容不一致')
                return receipt['result']
            workflow = self.repo.workflow(thread_id)
            if not workflow or workflow['status'] != 'waiting_input':
                raise ValueError('此流程没有待回答问题，可能已取消或记录已删除')
            pending = workflow['result']['pending_input']
            if pending['interrupt_id'] != payload['interrupt_id']:
                raise ValueError('待回答问题已变化，请刷新')
            if pending['kind'] == 'plan_confirmation':
                if type(payload.get('approved')) is not bool:
                    raise ValueError('需要接受或拒绝计划')
                answer = {'approved': payload['approved']}
                display = '接受计划' if payload['approved'] else '拒绝计划'
            else:
                if not payload.get('text'):
                    raise ValueError('请输入补充信息')
                answer = {'text': payload['text'], 'event_id': payload.get('event_id')}
                display = payload['text']
            self.repo.message(payload['request_id'], thread_id, 'user', display)
            state = self.graph_for(workflow['kind']).invoke(Command(resume=answer), {'configurable': {'thread_id': thread_id}})
            result = self.format_result(thread_id, state)
            result['duration_ms'] = round((time.perf_counter() - started) * 1000)
            self.repo.workflow_result(thread_id, result)
            self.repo.save_receipt(payload['request_id'], thread_id, fp, result)
            self.repo.message(payload['request_id'] + ':reply', thread_id, 'assistant', result['reply'], result['cards'],
                              result['evidence_refs'], process_trace=result['process_trace'], duration_ms=result['duration_ms'])
            return result

    def purge_checkpoints(self):
        threads = {item.config['configurable']['thread_id'] for item in self.checkpointer.list(None)}
        for thread_id in threads:
            self.checkpointer.delete_thread(thread_id)
        self.cp_conn.commit()
        self.cp_conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        self.cp_conn.execute('VACUUM')
        self.repo.conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        self.repo.conn.execute('VACUUM')

    def delete_event(self, event_id):
        with self.lock:
            self.repo.delete_event(event_id)
            self.purge_checkpoints()

    def archive(self, reference=None):
        with self.lock:
            months = archive(self.repo, reference)
            if months:
                self.purge_checkpoints()
            return months

    def prune_context(self, reference=None):
        with self.lock:
            moment = reference or now()
            completed_before = (moment - timedelta(days=30)).astimezone(timezone.utc).isoformat()
            pending_before = (moment - timedelta(days=366)).astimezone(timezone.utc).isoformat()
            rows = self.repo.conn.execute("SELECT id,kind FROM workflows WHERE (status!='waiting_input' AND created_at<?) OR (status='waiting_input' AND created_at<?)", (completed_before, pending_before)).fetchall()
            with self.repo.conn:
                for row in rows:
                    self.checkpointer.delete_thread(row['id'])
                    self.repo.conn.execute('DELETE FROM messages WHERE thread_id=?', (row['id'],))
                    self.repo.conn.execute('DELETE FROM receipts WHERE thread_id=?', (row['id'],))
                    self.repo.conn.execute('DELETE FROM workflows WHERE id=?', (row['id'],))
                    if row['kind'] == 'plan':
                        self.repo.conn.execute("UPDATE plans SET status='purged',data='{}' WHERE id=? AND status='pending'", (row['id'],))
                self.repo.conn.execute('DELETE FROM messages WHERE created_at<?', (completed_before,))
            return len(rows)
