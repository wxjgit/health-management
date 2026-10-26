"""Chat workflow nodes that turn user input into persistence-ready drafts."""

import json
import re

from ..common.clock import date_from_text, local_day
from ..chat.feedback import (
    attach_feedback,
    explicitly_recording,
    reconcile_feedback,
    wants_advice,
)
from ..chat.drafts import check_drafts, merge_patch
from ..evidence import assert_grounded, ground_patch
from ..modeling import PolicyError, record_projection
from ..health.nutrition import prepare_nutrition
from ..chat.replies import medical_question
from ..contracts.events import EventDraft


class ChatParsingMixin:
    """Parsing, normalization and nutrition preparation nodes."""

    @staticmethod
    def route_parse(state):
        """Choose the only parser stage allowed for the current turn."""
        if state.get('incremental_answer') and state.get('extraction'):
            return 'patch_pending'
        return 'parse_initial'

    def parse_initial(self, state):
        """Plan and materialize a fresh input without handling clarification patches."""
        calls_before = self.parser.boundary.call_count
        text = state['input_text']
        abandoned = list(state.get('abandoned_indexes', []))
        skip_optional = state.get('skip_optional', False)
        previous_feedback = state.get('conversation_feedback', [])
        supplied_feedback = []
        feedback_only = state.get('feedback_only', False)
        bound_id = state.get('selected_id')
        bound_version = state.get('correction_version')
        default_day = date_from_text(state.get('original') or text).date().isoformat()
        parsed, extra, proposed = self.harness.prepare(state)
        extraction = parsed.model_dump(mode='json')
        supplied_feedback = extraction.get('feedback', [])
        if parsed.correction_id:
            prior = self.repo.event(parsed.correction_id)
            previous_feedback = prior['data'].get('feedback', []) if prior else []
            if prior:
                default_day = prior['day']
                bound_id, bound_version = prior['id'], prior['version']
        # A model cannot introduce meal feedback from unrelated history.
        for event in extraction['events']:
            supplied_feedback.extend(event.get('feedback', []))
            event['feedback'] = []
        if any(w in text for w in ['不知道', '不清楚']):
            skip_optional = True
        source = 'model' if self.parser.boundary.call_count > calls_before else 'local'
        return {
            'extraction': extraction,
            'selected_id': bound_id,
            'correction_version': bound_version,
            'extra_actions': [a.model_dump(mode='json') for a in extra],
            'processing_plan': [a.model_dump(mode='json') for a in proposed],
            'plan_input_text': text,
            'parse_context': {
                'previous_feedback': previous_feedback,
                'supplied_feedback': supplied_feedback,
                'default_day': default_day,
                'feedback_only': feedback_only,
                'abandoned_indexes': abandoned,
                'skip_optional': skip_optional,
                'source': source,
                'incremental': False,
            },
        }

    def patch_pending(self, state):
        """Merge one clarification answer into the already bounded draft."""
        if not state.get('incremental_answer') or not state.get('extraction'):
            raise PolicyError('当前没有可补充的草稿，请重新提交完整输入。')
        calls_before = self.parser.boundary.call_count
        text = state['input_text']
        extraction = json.loads(json.dumps(state['extraction']))
        index = state.get('pending_event_index')
        if index is None or not 0 <= index < len(extraction['events']):
            raise PolicyError('待补充记录已经失效，请重新提交。')
        bound_version = state.get('correction_version')
        if extraction['intent'] == 'correct' and bound_version is not None:
            prior = self.harness.selected(state)
            if not prior or prior['version'] != bound_version:
                raise PolicyError('这条记录在补充期间已经变化，请重新选择后纠正。')
        abandoned = list(state.get('abandoned_indexes', []))
        skip_optional = state.get('skip_optional', False)
        previous_feedback = state.get('conversation_feedback', [])
        supplied_feedback = []
        feedback_only = state.get('feedback_only', False)
        default_day = date_from_text(state.get('original') or text).date().isoformat()
        occurred_at = extraction['events'][index].get('occurred_at')
        if occurred_at:
            # Relative dates are anchored once, even if the answer arrives next day.
            default_day = local_day(occurred_at)
        if 'conversation_feedback' not in state:
            previous_feedback = reconcile_feedback(
                [], [], state.get('original') or extraction['events'][index]['raw_text'], default_day
            )
        cancelled = any(w in text for w in ['先不记', '不记了', '取消记录'])
        unknown = text.strip().strip('，。,.!！') in (
            '不知道', '我不知道', '不清楚', '我不清楚', '忘了', '我忘了', '不记得', '我不记得'
        )
        consulting = (
            wants_advice(text)
            and not explicitly_recording(text)
            and not re.search(r'\d|吃了|喝了', text)
        )
        if consulting or (feedback_only and (cancelled or unknown)):
            # An optional feedback follow-up is not a mandatory logging form.
            extraction.update(intent='advice', events=[])
            feedback_only = False
        elif cancelled or (unknown and state.get('pending_required')):
            abandoned.append(index)
        elif unknown:
            skip_optional = True
        else:
            if self.parser.mode == 'model':
                self.parser.boundary.grant('patch_draft', {
                    'text': text,
                    'draft': record_projection(extraction['events'][index]),
                    'requested_fields': state['pending_fields'],
                    'item_index': state.get('pending_item_index'),
                })
            patch = self.parser.patch(
                text,
                extraction['events'][index],
                state['pending_fields'],
                state.get('pending_item_index'),
            )
            patch = ground_patch(
                patch,
                text,
                extraction['events'][index]['kind'],
                state['pending_fields'],
                state.get('pending_item_index'),
                state.get('source_message_id'),
            )
            supplied_feedback = patch.feedback
            extraction['events'][index] = merge_patch(
                extraction['events'][index],
                patch,
                state['pending_fields'],
                text,
                state.get('pending_item_index'),
            )
            if feedback_only and extraction['events'][index].get('items'):
                extraction['intent'] = 'record'
                feedback_only = False
        extraction.update(
            question=None,
            clarification_event_index=None,
            clarification_item_index=None,
            clarification_fields=[],
        )
        source = 'model' if self.parser.boundary.call_count > calls_before else 'local'
        return {
            'extraction': extraction,
            'parse_context': {
                'previous_feedback': previous_feedback,
                'supplied_feedback': supplied_feedback,
                'default_day': default_day,
                'feedback_only': feedback_only,
                'abandoned_indexes': abandoned,
                'skip_optional': skip_optional,
                'source': source,
                'incremental': True,
            },
        }

    def normalize_extraction(self, state):
        """Apply local feedback rules and expose one stable extraction contract."""
        context = state.get('parse_context')
        if not context:
            raise PolicyError('解析阶段缺少本地归一化上下文。')
        text = state['input_text']
        extraction = json.loads(json.dumps(state['extraction']))
        feedback = reconcile_feedback(
            context['previous_feedback'], context['supplied_feedback'], text, context['default_day']
        )
        extraction['feedback'] = feedback
        for event in extraction['events']:
            event['feedback'] = []
        attach_feedback(extraction['events'], feedback)
        feedback_only = context['feedback_only']
        incomplete_meals_only = extraction['events'] and all(
            event['kind'] == 'meal' and not event.get('items') for event in extraction['events']
        )
        if (
            not context['incremental']
            and extraction['intent'] in ('record', 'advice')
            and feedback
            and not explicitly_recording(text)
            and (incomplete_meals_only or not extraction['events'])
        ):
            extraction['intent'] = 'advice'
            extraction['question'] = None
            if (
                wants_advice(text)
                or medical_question(text)
                or not any(item['stance'] == 'experience' for item in feedback)
            ):
                extraction['events'] = []
                feedback_only = False
            else:
                feedback_only = True
                if not extraction['events']:
                    extraction['events'] = [{
                        'kind': 'meal',
                        'occurred_at': date_from_text(text).isoformat(),
                        'raw_text': text,
                        'items': [],
                        'feedback': feedback,
                    }]
        intent_labels = {
            'record': '记录健康信息',
            'correct': '纠正历史记录',
            'query': '查询历史',
            'advice': '获取建议',
            'fact': '保存长期信息',
        }
        return {
            'extraction': extraction,
            'incremental_answer': False,
            'conversation_feedback': feedback,
            'feedback_only': feedback_only,
            'abandoned_indexes': list(dict.fromkeys(context['abandoned_indexes'])),
            'skip_optional': context['skip_optional'],
            'parse_context': {},
            'process_trace': [*state.get('process_trace', []), {
                'title': '理解输入',
                'detail': f'已检查处理计划，当前执行“{intent_labels[extraction["intent"]]}”；历史记录按固定视图取数。',
                'source': context['source'],
            }],
        }

    def validate_drafts(self, state):
        checked = check_drafts(
            state['extraction'],
            state.get('rounds', 0),
            state.get('skip_optional', False),
            state.get('abandoned_indexes', []),
        )
        if state.get('feedback_only') and state['extraction'].get('events'):
            checked.update(
                pending_event_index=0,
                pending_fields=['items'],
                pending_required=False,
                pending_item_index=None,
            )
            checked['parsed']['question'] = '如果想一起复盘，这次主要吃了什么？不知道或不想记录也可以。'
        return {
            **checked,
            'process_trace': self.traced(
                state,
                '结构校验',
                '检查草稿缺失信息；完整记录通过字段范围、时区和纠错边界校验后才允许保存。',
            ),
        }

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

            def estimate(items):
                self.parser.boundary.grant('estimate_nutrition', {'items': items})
                return self.parser.estimate_nutrition(items)

            prepared[str(index)] = prepare_nutrition(
                validated,
                'local' if state.get('safety', {}).get('capability') == 'record_only' else self.parser.mode,
                estimate,
                previous=previous,
                user_text=state['input_text'],
                reuse_unavailable=cached is not None,
            )
            meals += validated['kind'] == 'meal'
        update = {'prepared_events': prepared}
        if meals:
            update['process_trace'] = self.traced(
                state,
                '营养准备',
                '按完整食物与实际分量准备营养；保留用户数值，校验估算与来源，无法估算时保留未知原因。',
            )
        return update

    def prepared_record(self, state, index):
        record = state.get('prepared_events', {}).get(str(index))
        if record is None:
            raise ValueError('记录尚未经过营养准备，不能保存')
        prior = None
        correction_id = state.get('parsed', {}).get('correction_id')
        if correction_id:
            stored = self.repo.event(correction_id)
            prior = stored['data'] if stored else None
        return assert_grounded(record, prior=prior).model_dump(mode='json')
