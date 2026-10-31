"""组装模块化 Chat Graph 的节点和路由。"""

from langgraph.graph import StateGraph, START, END

from .clarification_flow import ChatClarification
from .interpretation_flow import ChatInterpretation
from .operation_flow import ChatOperations
from .response_flow import ChatResponse
from .safety_flow import ChatSafetyFlow
from .state import ChatState


class ChatWorkflow:
    """Assemble focused chat components without extending Engine."""

    def __init__(self, runtime, dependencies, tool_executor):
        self.runtime = runtime
        self.safety = ChatSafetyFlow(runtime)
        self.interpretation = ChatInterpretation(dependencies)
        self.clarification = ChatClarification(dependencies)
        self.operations = ChatOperations(
            dependencies, self.interpretation, tool_executor, runtime
        )
        self.response = ChatResponse(dependencies)

    def build(self):
        graph = StateGraph(ChatState)
        self.safety.add_nodes(graph)
        nodes = {
            'parse_initial': self.interpretation.parse_initial,
            'patch_pending': self.interpretation.patch_pending,
            'normalize_extraction': self.interpretation.normalize_extraction,
            'validate_drafts': self.interpretation.validate_drafts,
            'authorize_operation': self.runtime.authorize_operation,
            'prepare_nutrition': self.interpretation.prepare_nutrition,
            'prepare_operations': self.operations.prepare_commands,
            'save_ready': self.operations.save_ready,
            'prepare_clarification': self.clarification.prepare,
            'clarify': self.clarification.wait_for_answer,
            'execute_operations': self.operations.execute,
            'route_response': self.response.prepare,
            'query_response': self.response.build_query,
            'fact_response': self.response.build_fact,
            'record_response': self.response.build_record,
            'advice_response': self.response.build_advice,
            'compose_response': self.response.compose,
        }
        for name, operation in nodes.items():
            graph.add_node(name, self.runtime.guarded_node(operation, node_name=name))

        parse_routes = {name: name for name in ('parse_initial', 'patch_pending')}
        safety_routes = {
            **parse_routes,
            'safety_response': 'safety_response',
            'classify_safety': 'classify_safety',
        }

        graph.add_edge(START, 'safety_gate')
        graph.add_conditional_edges(
            'safety_gate',
            lambda state: self.safety.entry_route(state, self.interpretation),
            safety_routes,
        )
        graph.add_conditional_edges(
            'classify_safety',
            lambda state: self.safety.classified_route(state, self.interpretation),
            safety_routes,
        )
        graph.add_edge('safety_response', END)
        graph.add_edge('parse_initial', 'normalize_extraction')
        graph.add_edge('patch_pending', 'normalize_extraction')
        graph.add_edge('normalize_extraction', 'validate_drafts')
        graph.add_edge('validate_drafts', 'authorize_operation')
        graph.add_conditional_edges(
            'authorize_operation',
            lambda state: (
                'prepare_nutrition'
                if state['safety']['category'] == 'general'
                else 'safety_response'
            ),
        )
        graph.add_edge('prepare_nutrition', 'prepare_operations')
        graph.add_edge('prepare_operations', 'save_ready')
        graph.add_conditional_edges(
            'save_ready',
            self.operations.route,
            {name: name for name in ('prepare_clarification', 'execute_operations', 'route_response')},
        )
        graph.add_edge('prepare_clarification', 'clarify')
        graph.add_edge('clarify', 'safety_gate')
        graph.add_edge('execute_operations', 'route_response')
        response_routes = {
            name: name for name in (
                'query_response', 'fact_response', 'record_response', 'advice_response'
            )
        }
        graph.add_conditional_edges('route_response', self.response.route, response_routes)
        for name in response_routes:
            graph.add_edge(name, 'compose_response')
        graph.add_edge('compose_response', END)
        return graph.compile(checkpointer=self.runtime.checkpointer)
