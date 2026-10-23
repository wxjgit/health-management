"""Serialize local workflow work without holding a global lock over model I/O."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from threading import Lock, RLock

from langgraph.checkpoint.sqlite import SqliteSaver


_execution = ContextVar('workflow_execution', default=None)
_node_engine = ContextVar('workflow_node_engine', default=None)


class WorkflowConflict(RuntimeError):
    """Stop obsolete work; model fallback handlers must not swallow this error."""


class KeyedLocks:
    """Keep independent requests/workflows separate, including queued callers."""

    def __init__(self):
        self._guard = Lock()
        self._entries = {}

    @contextmanager
    def hold(self, key):
        if key is None:
            yield
            return
        with self._guard:
            entry = self._entries.setdefault(key, [RLock(), 0])
            entry[1] += 1
        try:
            with entry[0]:
                yield
        finally:
            with self._guard:
                entry[1] -= 1
                if not entry[1]:
                    del self._entries[key]


@dataclass(frozen=True)
class WorkflowExecution:
    engine: object
    thread_id: str
    epoch: int


class GuardedSqliteSaver(SqliteSaver):
    """Guard background checkpoint writes as well as foreground graph nodes."""

    def __init__(self, conn, engine):
        super().__init__(conn)
        self.engine = engine

    @contextmanager
    def cursor(self, transaction=True):
        with self.engine.lock:
            self.engine.check_execution()
            with super().cursor(transaction) as cursor:
                yield cursor


class WorkflowConcurrencyMixin:
    def capture_execution(self, thread_id):
        # Called under engine.lock when the request is registered/located.
        return WorkflowExecution(self, thread_id, self.context_epoch)

    def check_execution(self, execution=None):
        execution = execution or _execution.get()
        if execution is None or execution.engine is not self:
            return
        if execution.epoch != self.context_epoch:
            raise WorkflowConflict('相关记录或上下文已清理，本次处理已停止，请重新提交。')
        workflow = self.repo.workflow(execution.thread_id)
        if not workflow or workflow['status'] == 'cancelled':
            raise WorkflowConflict('此流程已取消或记录已删除，本次处理已停止。')

    @contextmanager
    def serialized_execution(self, execution, conversation=None):
        # Never wait for these locks while holding engine.lock. Plan confirmation
        # does not take a conversation lock, so it can pass a slow chat request.
        conversation_key = ('conversation', conversation) if conversation is not None else None
        with self.execution_locks.hold(('workflow', execution.thread_id)):
            with self.execution_locks.hold(conversation_key):
                token = _execution.set(execution)
                try:
                    with self.lock:
                        self.check_execution()
                    yield
                finally:
                    _execution.reset(token)

    def guarded_node(self, operation):
        @wraps(operation)
        def guarded(state):
            with self.lock:
                self.check_execution()
                token = _node_engine.set(self)
                try:
                    return operation(state)
                finally:
                    _node_engine.reset(token)
        return guarded

    def _model_context_version(self):
        active = self.repo.active_plan()
        return (
            self.repo.version(),
            self.repo.get_meta('preferences_version', 0),
            active['id'] if active else None,
        )

    @contextmanager
    def model_io(self, purpose):
        # Only a guarded node owns this acquisition, on this same Python thread.
        # Graph invocation itself never owns engine.lock.
        if _node_engine.get() is not self:
            raise WorkflowConflict('模型请求必须在受保护的工作流步骤中执行。')
        self.check_execution()
        # Processing plans contain only current input, not mutable health history.
        version = self._model_context_version() if purpose != 'processing_plan' else None
        self.lock.release()
        try:
            yield
        finally:
            self.lock.acquire()
            self.check_execution()
            if version is not None and version != self._model_context_version():
                raise WorkflowConflict('模型处理期间相关数据已变化，请使用原请求重试。')
