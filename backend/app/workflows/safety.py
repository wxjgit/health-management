"""Safety nodes and replay-time capability checks shared by chat and plans."""
import re

from ..contracts.safety import SafetyContext
from ..health.fact_resolution import pending_decisions
from ..health.safety import (
    POLICY_VERSION, REPLIES, decision, fingerprint, local_safety,
    merge_classification, offline_record, offline_preference, require_plan_eligibility,
    unsafe_generated_text,
)


class SafetyWorkflowMixin:
    def safety_context(self, state):
        raw = state.get('safety_input_text', state.get('input_text', ''))
        sources = [{'source_id': 'current', 'text': raw}] if raw else []
        original = state.get('original')
        if original and original != raw:
            sources.append({'source_id': 'original', 'text': original})
        # Only an explicit continuation may add one prior user source. Never
        # forward assistant text, arbitrary record history or complete profiles.
        if not original and re.search(r'^(?:那|还|又|刚才|继续|还是)', raw):
            prior = self.repo.safety_previous_input(state.get('conversation_id', 'main'), state['thread_id'])
            if prior:
                sources.append({'source_id': 'previous_user', 'text': prior})
        profile = self.repo.get_meta('profile') or {}
        view = self.repo.resolve_facts(purpose='safety')
        facts = {key: view[key] for key in ('constraints', 'conflicts', 'pending', 'reference_date', 'policy_version')}
        facts['pending'] = pending_decisions(view)
        context = SafetyContext.model_validate({
            'sources': sources or [{'source_id': 'current', 'text': '无文字附言'}],
            'profile': {key: profile[key] for key in ('age', 'nutrition_context') if key in profile},
        }).model_dump()
        signature = fingerprint(context, profile, facts, self.settings.model_name)
        return context, signature

    def safety_gate(self, state):
        context, signature = self.safety_context(state)
        result = local_safety(self.repo, [source['text'] for source in context['sources']])
        if result['category'] == 'general' and self.parser.mode != 'model':
            raw = state.get('safety_input_text', state.get('input_text', ''))
            if offline_record(raw, pending=bool(state.get('rounds'))):
                result = decision('general', 'offline_full_match', capability='record_only')
            elif offline_preference(raw):
                result = decision('general', 'offline_preference_match', capability='preference_only')
            elif not raw and state.get('plan') is not None:
                result = decision('general', 'no_plan_note', capability='plan')
            else:
                result = decision('uncertain', 'semantic_classifier_unavailable')
        return {'safety': result, 'safety_context': context, 'safety_fingerprint': signature,
                'process_trace': [*state.get('process_trace', []), {
                    'title': '本地安全检查', 'detail': f'{POLICY_VERSION}：{result["reason"]}。', 'source': 'local',
                }]}

    @staticmethod
    def route_safety_gate(state):
        if state['safety']['category'] != 'general':
            return 'safety_response'
        if state['safety']['reason'] == 'no_local_signal':
            return 'classify_safety'
        return 'authorized'

    def classify_safety(self, state):
        # No free text to classify for a plan with no note; eligibility remains local.
        raw = state.get('safety_input_text', state.get('input_text', ''))
        if state.get('plan') is not None and not raw and not state.get('original'):
            return {'safety': decision('general', 'no_plan_note', capability='plan')}
        context = state['safety_context']
        cached = state.get('safety_classification')
        try:
            if cached and cached.get('fingerprint') == state['safety_fingerprint']:
                value = cached['result']
            else:
                self.parser.boundary.grant('safety_classification', {'context': context})
                value = self.parser.classify_safety(context).model_dump()
            result = merge_classification(value, context)
            return {'safety': result, 'process_trace': [*state.get('process_trace', []), {
                'title': '独立安全分类', 'detail': '分类结果经本地结构、原文证据和风险边界校验。',
                'source': 'local' if cached and cached.get('fingerprint') == state['safety_fingerprint'] else 'model',
            }], 'safety_classification': {
                'fingerprint': state['safety_fingerprint'], 'result': value,
            }}
        except (ValueError, TypeError):
            return {'safety': decision('uncertain', 'classifier_failed')}

    def route_safety_result(self, state):
        return 'authorized' if state['safety']['category'] == 'general' else 'safety_response'

    def safety_response(self, state):
        result = state['safety']
        reply = REPLIES[result['category']]
        if state.get('record_ids'):
            reply += f'\n本流程此前已保存 {len(set(state["record_ids"]))} 条独立记录，本次补充没有保存。'
        return {'reply': reply, 'cards': [], 'evidence_refs': [], 'extra_actions': [],
                'reply_plan': {}, 'process_trace': [*state.get('process_trace', []), {
                    'title': '本地安全边界', 'detail': f'{POLICY_VERSION}：{result["category"]}；{result["reason"]}。',
                    'source': 'local',
                }]}

    def require_safety(self, state, operation):
        """Revalidate before sensitive nodes, including old checkpoint re-entry."""
        context, signature = self.safety_context(state)
        local = local_safety(self.repo, [source['text'] for source in context['sources']])
        if local['category'] != 'general':
            raise ValueError(REPLIES[local['category']])
        result = state.get('safety', {})
        unchanged = state.get('safety_fingerprint') == signature or (
            operation == 'activate_plan' and result.get('reason') == 'no_plan_note'
            and not state.get('safety_input_text', state.get('input_text', ''))
            and not state.get('original')
        )
        if (result.get('category') != 'general' or not unchanged
                or result.get('policy_version') != POLICY_VERSION):
            raise ValueError(REPLIES['uncertain'])
        if result.get('capability') == 'record_only':
            parsed = state.get('parsed', state.get('extraction', {}))
            if parsed and (parsed.get('intent') not in ('record', 'correct', 'query')
                           or state.get('extra_actions')):
                raise ValueError(REPLIES['uncertain'])
        if result.get('capability') == 'preference_only':
            parsed = state.get('parsed', {})
            if parsed and (parsed.get('intent') != 'fact' or state.get('extra_actions')):
                raise ValueError(REPLIES['uncertain'])
        if operation in ('prepare_plan', 'activate_plan'):
            require_plan_eligibility(self.repo, state.get('plan', {}).get('targets'), state.get('input_text', ''))

    def authorize_operation(self, state):
        try:
            self.require_safety(state, 'prepare_nutrition')
            parsed = state.get('parsed', {})
            if unsafe_generated_text(parsed.get('question') or ''):
                raise ValueError('unapproved_question')
            for raw in [state.get('original', ''), state.get('input_text', '')]:
                if local_safety(self.repo, [raw])['category'] != 'general':
                    raise ValueError('risk_recheck')
        except ValueError:
            return {'safety': decision('uncertain', 'operation_not_authorized')}
        return {}

    def add_safety_nodes(self, graph):
        for name in ('safety_gate', 'classify_safety', 'safety_response'):
            graph.add_node(name, self.guarded_node(getattr(self, name)))
