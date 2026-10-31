"""Chat routing around the shared safety policy implementation."""

from .component import ChatComponent


class ChatSafetyFlow(ChatComponent):
    """Expose shared safety nodes through a chat-specific routing boundary."""

    def add_nodes(self, graph):
        self.engine.add_safety_nodes(graph)

    def entry_route(self, state, interpretation):
        route = self.engine.route_safety_gate(state)
        return interpretation.route_parse(state) if route == 'authorized' else route

    def classified_route(self, state, interpretation):
        if self.engine.route_safety_result(state) == 'authorized':
            return interpretation.route_parse(state)
        return 'safety_response'
