"""Shared dependency access for chat workflow components."""


class ChatComponent:
    """A focused chat component backed by the application Engine services.

    Components deliberately use composition rather than adding more Engine
    mixins. Only the services declared below cross the component boundary.
    """

    def __init__(self, engine):
        self.engine = engine

    @property
    def repo(self):
        return self.engine.repo

    @property
    def parser(self):
        return self.engine.parser

    @property
    def harness(self):
        return self.engine.harness

    def safety_context(self, state):
        return self.engine.safety_context(state)

    @staticmethod
    def traced(state, title, detail):
        return [
            *state.get('process_trace', []),
            {'title': title, 'detail': detail, 'source': 'local'},
        ]
