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

from .clock import now, date_from_text
from .nutrition import estimate
from .parser import Parser
from .repository import Repository, dump
from .services import dashboard, evidence, recommendation, meal_assessment, analyze, review, plan_draft, archive


class State(TypedDict, total=False):
    thread_id: str
    input_text: str
    original: str
    selected_id: str | None
    parsed: dict[str, Any]
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
        parsed = self.parser.parse(state['input_text'], self.recent(state.get('selected_id')),
                                   state.get('selected_id'), state.get('original'))
        if state.get('rounds', 0) >= 2 and parsed.events:
            parsed.question = None
        intent_labels = {'record': '记录健康信息', 'correct': '纠正历史记录', 'query': '查询历史',
                         'advice': '获取建议', 'fact': '保存长期信息'}
        source = 'model' if self.settings.mode == 'model' else 'local'
        return {'parsed': parsed.model_dump(mode='json'), 'process_trace': [
            {'title': '理解输入', 'detail': f'识别为“{intent_labels[parsed.intent]}”，提取日期、类型和相关字段。',
             'source': source},
            {'title': '结构校验', 'detail': '使用预定义数据结构检查字段范围、时区和纠错边界。', 'source': 'local'},
        ]}

    @staticmethod
    def traced(state, title, detail):
        return [*state.get('process_trace', []), {'title': title, 'detail': detail, 'source': 'local'}]

    def save_ready(self, state):
        parsed = state['parsed']
        if parsed['intent'] != 'record' or not parsed.get('question'):
            return {}
        question = parsed['question']
        pending_kind = 'exercise' if '运动' in question else 'sleep' if '睡' in question else 'meal'
        ids = list(state.get('record_ids', []))
        indexes = list(state.get('saved_indexes', []))
        for index, draft in enumerate(parsed['events']):
            if draft['kind'] != pending_kind and index not in indexes:
                saved = self.repo.save_events([estimate(draft, self.settings.mode)], f'{state["thread_id"]}:event:{index}')
                ids.extend(saved)
                indexes.append(index)
        return {'record_ids': ids, 'saved_indexes': indexes}

    def route_chat(self, state):
        parsed = state['parsed']
        if parsed.get('question'):
            return 'clarify'
        if parsed['intent'] in ['record', 'correct'] and parsed['events']:
            return 'persist'
        if parsed['intent'] == 'fact' and parsed.get('fact_text'):
            return 'fact'
        return 'respond'

    def clarify(self, state):
        parsed = state['parsed']
        payload = {'kind': 'clarification', 'question': parsed['question'],
                   'saved_records': len(state.get('record_ids', []))}
        if parsed['intent'] == 'correct' and not parsed.get('correction_id'):
            payload['options'] = [{'id': e['id'], 'label': f'{e["day"]} {e["raw_text"]}'} for e in self.recent()[:10]]
        answer = interrupt(payload)
        if not isinstance(answer, dict) or not isinstance(answer.get('text'), str):
            raise ValueError('需要文字补充')
        selected = answer.get('event_id') or parsed.get('correction_id') or state.get('selected_id')
        if parsed['intent'] == 'correct':
            text = state.get('original') or state['input_text']
            if not answer.get('event_id'):
                text += '，' + answer['text']
            return {'input_text': text, 'selected_id': selected, 'rounds': state.get('rounds', 0) + 1}
        return {'original': state.get('original') or state['input_text'], 'input_text': answer['text'],
                'rounds': state.get('rounds', 0) + 1}

    def persist(self, state):
        parsed = state['parsed']
        ids = list(state.get('record_ids', []))
        for index, draft in enumerate(parsed['events']):
            if index not in state.get('saved_indexes', []):
                ids.extend(self.repo.save_events([estimate(draft, self.settings.mode)],
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
            reply = ('已修正记录并重新计算相关日期。' if parsed['intent'] == 'correct' else f'已保存 {len(records)} 条记录。')
            if any(e['kind'] == 'meal' for e in records):
                advice, historical = recommendation(self.repo, records[0]['day'])
                reply += '\n' + advice
                refs = list({r['id']: r for r in refs + historical}.values())
            if self.settings.mode == 'demo' and cards:
                reply += '\n当前为演示模式，营养数据来自固定示例值，请勿将其当成精确摄入。'
        else:
            reply, refs = recommendation(self.repo)
            if self.settings.mode == 'demo':
                reply += '\n演示解析支持常见食物、体重、睡眠、运动和周期表达；自由复杂输入请接入模型。'
        if parsed['intent'] != 'query':
            trace = list(state.get('process_trace', []))
        trace.append({'title': '生成结果', 'detail': '由本地业务规则整理回复、卡片和可追溯依据。', 'source': 'local'})
        return {'reply': reply, 'cards': cards, 'evidence_refs': refs, 'process_trace': trace}

    def build_chat(self):
        graph = StateGraph(State)
        for name in ['parse', 'save_ready', 'clarify', 'persist', 'fact', 'respond']:
            graph.add_node(name, getattr(self, name))
        graph.add_edge(START, 'parse')
        graph.add_edge('parse', 'save_ready')
        graph.add_conditional_edges('save_ready', self.route_chat, {n: n for n in ['clarify', 'persist', 'fact', 'respond']})
        graph.add_edge('clarify', 'parse')
        graph.add_edge('persist', 'respond')
        graph.add_edge('fact', 'respond')
        graph.add_edge('respond', END)
        return graph.compile(checkpointer=self.checkpointer)

    def prepare_plan(self, state):
        plan = plan_draft(self.repo, state.get('plan', {}).get('targets'), state.get('input_text', ''))
        plan_id = state.get('plan_id') or state['thread_id']
        self.repo.save_plan(plan_id, state['thread_id'], plan)
        # On replay after an already committed insert, reuse the persisted immutable proposal.
        return {'plan': self.repo.plan(plan_id)['data'], 'plan_id': plan_id}

    def approve_plan(self, state):
        plan = state['plan']
        if not plan['needs_confirmation']:
            return {'approved': True}
        answer = interrupt({'kind': 'plan_confirmation', 'question': '是否采用这份计划？确认前当前目标保持不变。',
                            'plan_id': state['plan_id'], 'targets': plan['targets'], 'changes': plan['changes']})
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
