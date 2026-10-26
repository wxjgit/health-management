"""Chat workflow routing, persistence and graph construction."""

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt

from ..modeling import PolicyError
from ..chat.replies import express_plan, make_plan, medical_question
from .state import State


class ChatFlowMixin:
    """State routing and write-side chat nodes."""

    @staticmethod
    def traced(state, title, detail):
        return [
            *state.get('process_trace', []),
            {'title': title, 'detail': detail, 'source': 'local'},
        ]

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
        candidates = []
        if state['parsed']['intent'] == 'correct' and not state['parsed'].get('correction_id'):
            candidates = [
                {'id': event['id'], 'label': f'{event["day"]} {event["raw_text"][:120]}'}
                for event in self.repo.events(limit=5)
            ]
        plan = make_plan(
            state.get('conversation_feedback', []),
            'clarify',
            guided_topics=state.get('guided_topics', []),
            medical=medical_question(state.get('original') or state['input_text']),
            include_guidance=state.get('safety', {}).get('capability') != 'record_only',
        )
        plan['context'] = {
            'foods': list(dict.fromkeys(
                item['name']
                for event in state['extraction']['events']
                for item in event.get('items', [])
                if item.get('name')
            ))[:3]
        }
        narrative, trace = express_plan(self.parser, plan)
        reply = '\n'.join(part for part in [narrative, state['parsed']['question']] if part)
        return {
            'clarification_reply': reply,
            'reply_plan': plan,
            'correction_candidates': candidates,
            'guidance_shown': state.get('guidance_shown', False) or bool(plan['points']),
            'guided_topics': list(dict.fromkeys([
                *state.get('guided_topics', []),
                *(point['topic'] for point in plan['points'] if point['role'] == 'guidance'),
            ])),
            'process_trace': (
                [*state.get('process_trace', []), trace]
                if plan['points'] else state.get('process_trace', [])
            ),
        }

    def clarify(self, state):
        parsed = state['parsed']
        payload = {
            'kind': 'clarification',
            'question': state.get('clarification_reply') or parsed['question'],
            'prompt': parsed['question'],
            'saved_records': len(state.get('record_ids', [])),
            'requested_fields': state.get('pending_fields', []),
            'optional': not state.get('pending_required', True),
        }
        if parsed['intent'] == 'correct' and not parsed.get('correction_id'):
            # These are local UI choices, never a list sent to the model.
            payload['options'] = state.get('correction_candidates', [])
        answer = interrupt(payload)
        if not isinstance(answer, dict) or not isinstance(answer.get('text'), str):
            raise ValueError('需要文字补充')
        selected = answer.get('event_id') or parsed.get('correction_id') or state.get('selected_id')
        if answer.get('event_id') and answer['event_id'] not in {
            option['id'] for option in payload.get('options', [])
        }:
            raise PolicyError('请选择本次提供的纠错候选记录。')
        if state.get('pending_event_index') is not None:
            return {
                'safety_input_text': answer['text'],
                'original': state.get('original') or state['input_text'],
                'input_text': answer['text'],
                'source_message_id': answer.get(
                    'source_message_id', state.get('source_message_id')
                ),
                'source_text': answer['text'],
                'selected_id': selected,
                'incremental_answer': True,
                'rounds': state.get('rounds', 0) + 1,
            }
        if parsed['intent'] == 'correct':
            text = state.get('original') or state['input_text']
            if not answer.get('event_id'):
                text += '，' + answer['text']
            return {
                'safety_input_text': answer['text'],
                'input_text': text,
                'source_message_id': (
                    state.get('source_message_id')
                    if answer.get('event_id')
                    else answer.get('source_message_id', state.get('source_message_id'))
                ),
                'source_text': (
                    state.get('source_text') if answer.get('event_id') else answer['text']
                ),
                'selected_id': selected,
                'rounds': state.get('rounds', 0) + 1,
            }
        return {
            'safety_input_text': answer['text'],
            'original': state.get('original') or state['input_text'],
            'input_text': answer['text'],
            'source_message_id': answer.get('source_message_id', state.get('source_message_id')),
            'source_text': answer['text'],
            'rounds': state.get('rounds', 0) + 1,
        }

    def persist(self, state):
        parsed = state['parsed']
        if parsed.get('question'):
            raise ValueError('待补充的记录不能直接保存')
        if parsed['intent'] == 'correct':
            # Rebind before every write, including checkpoint replay.
            bound = self.harness.resolve(state, None)
            if not bound or bound['id'] != parsed.get('correction_id'):
                raise PolicyError('纠错目标已失效，请重新选择记录。')
            if (
                state.get('correction_version') is not None
                and bound['version'] != state['correction_version']
            ):
                raise PolicyError('纠错记录的版本已经变化，请重新选择记录。')
        ids = list(state.get('record_ids', []))
        for index, draft in zip(state['event_indexes'], parsed['events']):
            if index not in state.get('saved_indexes', []):
                validated = self.prepared_record(state, index)
                ids.extend(self.repo.save_events(
                    [validated],
                    f'{state["thread_id"]}:event:{index}',
                    parsed.get('correction_id'),
                ))
        return {
            'record_ids': list(dict.fromkeys(ids)),
            'process_trace': self.traced(
                state,
                '保存记录',
                f'已由本地程序写入或更新 {len(set(ids))} 条健康记录。',
            ),
        }

    def fact(self, state):
        self.repo.save_fact(state['thread_id'], state['parsed']['fact_text'])
        return {
            'safety_fingerprint': self.safety_context(state)[1],
            'process_trace': self.traced(
                state,
                '保存长期信息',
                '已由本地程序保存用户明确表达的偏好或限制。',
            )
        }

    def build_chat(self):
        graph = StateGraph(State)
        self.add_safety_nodes(graph)
        node_names = [
            'parse_initial',
            'patch_pending',
            'normalize_extraction',
            'validate_drafts',
            'authorize_operation',
            'prepare_nutrition',
            'save_ready',
            'prepare_clarification',
            'clarify',
            'persist',
            'fact',
            'respond',
        ]
        for name in node_names:
            graph.add_node(name, self.guarded_node(getattr(self, name)))

        parse_routes = {name: name for name in ['parse_initial', 'patch_pending']}
        graph.add_edge(START, 'safety_gate')
        safety_routes = {**parse_routes, 'safety_response': 'safety_response', 'classify_safety': 'classify_safety'}
        def entry(state):
            route = self.route_safety_gate(state)
            return self.route_parse(state) if route == 'authorized' else route
        def classified(state):
            return self.route_parse(state) if self.route_safety_result(state) == 'authorized' else 'safety_response'
        graph.add_conditional_edges('safety_gate', entry, safety_routes)
        graph.add_conditional_edges('classify_safety', classified, safety_routes)
        graph.add_edge('safety_response', END)
        graph.add_edge('parse_initial', 'normalize_extraction')
        graph.add_edge('patch_pending', 'normalize_extraction')
        graph.add_edge('normalize_extraction', 'validate_drafts')
        graph.add_edge('validate_drafts', 'authorize_operation')
        graph.add_conditional_edges('authorize_operation', lambda state:
                                    'prepare_nutrition' if state['safety']['category'] == 'general' else 'safety_response')
        graph.add_edge('prepare_nutrition', 'save_ready')
        graph.add_conditional_edges(
            'save_ready',
            self.route_chat,
            {name: name for name in ['prepare_clarification', 'persist', 'fact', 'respond']},
        )
        graph.add_edge('prepare_clarification', 'clarify')
        graph.add_edge('clarify', 'safety_gate')
        graph.add_edge('persist', 'respond')
        graph.add_edge('fact', 'respond')
        graph.add_edge('respond', END)
        return graph.compile(checkpointer=self.checkpointer)
