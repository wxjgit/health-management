"""为聊天工作流组件提供受限的公共依赖入口。"""


class ChatComponent:
    """A focused chat component backed by the application Engine services.

    Components deliberately use composition rather than adding more Engine
    mixins. Only the services declared below cross the component boundary.
    """

    def __init__(self, dependencies):
        self._dependencies = dependencies

    @property
    def repo(self):
        return self._dependencies.reader

    @property
    def parser(self):
        return self._dependencies.parser

    @property
    def harness(self):
        return self._dependencies.harness

    def safety_context(self, state):
        return self._dependencies.safety_context(state)

    @staticmethod
    def traced(state, title, detail):
        return [
            *state.get('process_trace', []),
            {'title': title, 'detail': detail, 'source': 'local'},
        ]
