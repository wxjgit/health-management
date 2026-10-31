"""把共享安全策略接入聊天流程并负责安全路由。"""

class ChatSafetyFlow:
    """Expose shared safety nodes through a chat-specific routing boundary."""

    def __init__(self, runtime):
        self.runtime = runtime

    def add_nodes(self, graph):
        self.runtime.add_safety_nodes(graph)

    def entry_route(self, state, interpretation):
        route = self.runtime.route_safety_gate(state)
        return interpretation.route_parse(state) if route == 'authorized' else route

    def classified_route(self, state, interpretation):
        if self.runtime.route_safety_result(state) == 'authorized':
            return interpretation.route_parse(state)
        return 'safety_response'
