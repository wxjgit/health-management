"""Clarification rendering, interruption and bounded resume handling."""

from langgraph.types import interrupt

from ...chat.replies import express_plan, make_plan, medical_question
from ...common.errors import DomainValidationError, ModelOutputError
from ...policy.output import default_feedback_selection, question_from_state, render_feedback
from .component import ChatComponent
from .contracts import active_clarification


class ChatClarification(ChatComponent):
    """Own the user-input pause/resume boundary for chat."""

    def prepare(self, state):
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
        narrative, trace, selection = express_plan(self.parser, plan, with_selection=True)
        reply = '\n'.join(part for part in [narrative, question_from_state(state)] if part)
        return {
            'clarification_reply': reply,
            'clarification_selection': selection,
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

    def wait_for_answer(self, state):
        parsed = state['parsed']
        task = active_clarification(state)
        # Checkpoint prose is not an execution receipt or a reusable output grant.
        question = question_from_state(state)
        plan = state.get('reply_plan') or make_plan([], 'clarify')
        selection = state.get('clarification_selection') or default_feedback_selection(plan)
        try:
            narrative = render_feedback(selection, plan)
        except ModelOutputError:
            narrative = render_feedback(default_feedback_selection(plan), plan)
        payload = {
            'kind': 'clarification',
            'question': '\n'.join(part for part in [narrative, question] if part),
            'prompt': question,
            'saved_records': len(state.get('record_ids', [])),
            'requested_fields': task.requested_fields if task else [],
            'optional': not task.required if task else False,
        }
        if parsed['intent'] == 'correct' and not parsed.get('correction_id'):
            payload['options'] = state.get('correction_candidates', [])
        answer = interrupt(payload)
        if not isinstance(answer, dict) or not isinstance(answer.get('text'), str):
            raise DomainValidationError()
        selected = answer.get('event_id') or parsed.get('correction_id') or state.get('selected_id')
        if answer.get('event_id') and answer['event_id'] not in {
            option['id'] for option in payload.get('options', [])
        }:
            raise DomainValidationError()
        if task and task.event_index is not None:
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
