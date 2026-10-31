"""提供不暴露数据库连接和写方法的聊天运行时 facade。"""

from dataclasses import dataclass

from .contracts import ToolExecutionContext


class ChatReadFacade:
    """只暴露 Chat 理解、回复和策略确实需要的读取能力。"""

    __slots__ = ('__repo',)

    def __init__(self, repo):
        self.__repo = repo

    def event(self, event_id):
        return self.__repo.event(event_id)

    def events(self, start=None, end=None, kind=None, search=None, limit=None):
        return self.__repo.events(start, end, kind, search, limit)

    def fact(self, fact_id):
        return self.__repo.fact(fact_id)

    def facts(self, include_inactive=False, reference_date=None, purpose='advice'):
        return self.__repo.facts(include_inactive, reference_date, purpose)

    def resolve_facts(self, reference_date=None, purpose='advice'):
        return self.__repo.resolve_facts(reference_date, purpose)

    def get_meta(self, key, default=None):
        return self.__repo.get_meta(key, default)

    def active_plan(self):
        return self.__repo.active_plan()

    def previous_reference_ids(self, conversation_id, excluding_thread):
        return self.__repo.previous_reference_ids(conversation_id, excluding_thread)

    def fact_versions(self):
        return self.__repo.fact_versions()


class ChatRuntimeFacade:
    """把 Engine 的聊天编排能力收窄为固定调用，不向组件暴露 Engine。"""

    __slots__ = (
        '__add_safety_nodes', '__authorize_operation', '__guarded_node',
        '__require_safety', '__route_safety_gate', '__route_safety_result',
        '__safety_context', '__workflow_generation', '_checkpointer', '_user_id',
    )

    def __init__(self, engine):
        self.__add_safety_nodes = engine.add_safety_nodes
        self.__authorize_operation = engine.authorize_operation
        self.__guarded_node = engine.guarded_node
        self.__require_safety = engine.require_safety
        self.__route_safety_gate = engine.route_safety_gate
        self.__route_safety_result = engine.route_safety_result
        self.__safety_context = engine.safety_context
        self.__workflow_generation = engine.repo.workflow_generation
        self._checkpointer = engine.checkpointer
        self._user_id = engine.user_id

    @property
    def checkpointer(self):
        return self._checkpointer

    def add_safety_nodes(self, graph):
        return self.__add_safety_nodes(graph)

    def authorize_operation(self, state):
        return self.__authorize_operation(state)

    def guarded_node(self, operation, *, node_name):
        return self.__guarded_node(operation, node_name=node_name)

    def route_safety_gate(self, state):
        return self.__route_safety_gate(state)

    def route_safety_result(self, state):
        return self.__route_safety_result(state)

    def safety_context(self, state):
        return self.__safety_context(state)

    def execution_context(self, state, operation_id):
        # 恢复 checkpoint 时也重新校验，不把已保存的 safety 字段当授权票据。
        self.__require_safety(state, 'execute_operations')
        safety = state.get('safety', {})
        capability = safety.get('capability')
        granted = capability if capability in ('record_only', 'preference_only') else 'full'
        return ToolExecutionContext(
            user_id=self._user_id,
            thread_id=state['thread_id'],
            operation_id=operation_id,
            workflow_generation=self.__workflow_generation(state['thread_id']),
            granted_capability=granted,
            policy_version=safety['policy_version'],
        )


@dataclass(frozen=True)
class ChatDependencies:
    reader: ChatReadFacade
    parser: object
    harness: object
    safety_context: object
