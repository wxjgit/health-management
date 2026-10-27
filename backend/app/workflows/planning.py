"""Plan confirmation and periodic analysis workflow graphs."""

from datetime import date

from ..common.errors import DomainValidationError, SafetyPolicyError

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt

from ..health import analyze, plan_draft, review
from .state import State


class PlanningWorkflowMixin:
    def prepare_plan(self, state):
        requested = state.get('plan', {})
        plan = plan_draft(
            self.repo,
            requested.get('targets'),
            state.get('input_text', ''),
            requested.get('target_source', 'manual'),
            requested.get('suggestion_fingerprint'),
            state.get('schedule_anchor'),
        )
        plan_id = state.get('plan_id') or state['thread_id']
        self.repo.save_plan(plan_id, state['thread_id'], plan)
        # On replay after an already committed insert, reuse the persisted immutable proposal.
        return {'plan': self.repo.plan(plan_id)['data'], 'plan_id': plan_id}

    def approve_plan(self, state):
        plan = state['plan']
        if not plan['needs_confirmation']:
            return {'approved': True}
        answer = interrupt({
            'kind': 'plan_confirmation',
            'question': '是否采用这份计划？确认前当前目标保持不变。',
            'plan_id': state['plan_id'],
            'targets': plan['targets'],
            'changes': plan['changes'],
            'target_source': plan.get('target_source', 'manual'),
            'target_basis': (plan.get('target_metadata') or {}).get('basis'),
            'target_warnings': (plan.get('target_metadata') or {}).get('warnings', []),
        })
        if not isinstance(answer, dict) or type(answer.get('approved')) is not bool:
            raise DomainValidationError()
        if answer['approved'] and answer.get('text'):
            updated = {**state, 'safety_input_text': answer['text'],
                       'original': state.get('input_text', '')}
            checked = self.safety_gate(updated)
            if self.route_safety_gate(checked) == 'classify_safety':
                checked.update(self.classify_safety({**updated, **checked}))
            if checked['safety']['category'] != 'general':
                raise SafetyPolicyError(checked['safety']['category'])
            return {**checked, 'safety_input_text': answer['text'],
                    'original': state.get('input_text', ''), 'approved': True}
        return {'approved': answer['approved']}

    def activate_plan(self, state):
        status = self.repo.decide_plan(state['plan_id'], state['approved'],
            decision_source='manual' if state['plan']['needs_confirmation'] else 'automatic')
        replies = {
            'active': '计划已生效。可在计划页查看下一餐、次日重点和一周安排。',
            'rejected': '已拒绝这份草案，原计划保持不变。',
            'stale': '确认期间记录或生效计划发生变化，这份草案已过期。请重新生成，当前目标保持不变。',
        }
        return {
            'reply': replies.get(status, '草案已处理。'),
            'cards': [],
            'evidence_refs': state['plan']['evidence_refs'],
        }

    def build_plan(self):
        graph = StateGraph(State)
        self.add_safety_nodes(graph)
        graph.add_node('prepare', self.guarded_node(self.prepare_plan))
        graph.add_node('approve', self.guarded_node(self.approve_plan))
        graph.add_node('activate', self.guarded_node(self.activate_plan))
        graph.add_edge(START, 'safety_gate')
        routes = {'authorized': 'prepare', 'classify_safety': 'classify_safety', 'safety_response': 'safety_response'}
        graph.add_conditional_edges('safety_gate', self.route_safety_gate, routes)
        graph.add_conditional_edges('classify_safety', self.route_safety_result, routes)
        graph.add_edge('safety_response', END)
        graph.add_edge('prepare', 'approve')
        graph.add_edge('approve', 'activate')
        graph.add_edge('activate', END)
        return graph.compile(checkpointer=self.checkpointer)

    def build_analysis(self):
        graph = StateGraph(State)
        graph.add_node('analyze', self.guarded_node(lambda state: {'analysis': analyze(
            self.repo, date.fromisoformat(state['period_end']) if state.get('period_end') else None)}, node_name='analyze'))
        graph.add_node('review', self.guarded_node(lambda state: {
            'cards': [{
                'type': 'review',
                **review(self.repo, state.get('period', 'week'),
                         date.fromisoformat(state['period_end']) if state.get('period_end') else None),
            }],
            'reply': '复盘已更新。规律仅引用足够完整的记录；数据不足时不会制造结论。',
        }, node_name='review'))
        graph.add_edge(START, 'analyze')
        graph.add_edge('analyze', 'review')
        graph.add_edge('review', END)
        return graph.compile(checkpointer=self.checkpointer)
