"""Serialize local workflow work without holding a global lock over model I/O."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from threading import Lock, RLock
import time

from langgraph.checkpoint.sqlite import SqliteSaver
from ..common.errors import ConflictError
from ..common.execution_budget import BudgetLock, check_budget
from ..privacy.provenance import ReadSet, capture, current_reads


_execution = ContextVar('workflow_execution', default=None)
_node_engine = ContextVar('workflow_node_engine', default=None)


class WorkflowConflict(ConflictError):
    """Stop obsolete work; model fallback handlers must not swallow this error."""

    default_code = 'workflow_execution_conflict'


class RequestErased(WorkflowConflict):
    """An old request may not recreate explicitly erased content."""


class KeyedLocks:
    """Keep independent requests/workflows separate, including queued callers."""

    def __init__(self):
        self._guard = Lock()
        self._entries = {}

    @contextmanager
    def hold(self, key, blocking=True):
        if key is None:
            yield True
            return
        with self._guard:
            role = 'conversation' if isinstance(key, tuple) and key[0] == 'conversation' else 'workflow'
            entry = self._entries.setdefault(key, [BudgetLock(role=role), 0])
            entry[1] += 1
        acquired = False
        try:
            acquired = entry[0].acquire(blocking=blocking)
            yield acquired
        finally:
            if acquired:
                entry[0].release()
            with self._guard:
                entry[1] -= 1
                if not entry[1]:
                    del self._entries[key]


@dataclass(frozen=True)
class WorkflowExecution:
    engine: object
    thread_id: str
    epoch: int
    generation: int


class GuardedSqliteSaver(SqliteSaver):
    """Guard background checkpoint writes as well as foreground graph nodes."""

    def __init__(self, conn, engine):
        super().__init__(conn)
        self.engine = engine

    @contextmanager
    def cursor(self, transaction=True):
        with self.engine.lock, self.lock:
            self.engine.check_execution()
            self.setup()
            cursor = self.conn.cursor()
            started = time.monotonic()
            try:
                try:
                    yield cursor
                finally:
                    # Cursor writes bypass Connection.execute; commit/rollback
                    # still pass through the measured BudgetConnection.
                    if self.conn.in_transaction and getattr(self.conn, '_transaction_started', None) is None:
                        self.conn._transaction_started = started
                self.engine.check_execution()
                if transaction:
                    self.conn.commit()
            except BaseException:
                if transaction:
                    self.conn.rollback()
                raise
            finally:
                cursor.close()


class WorkflowConcurrencyMixin:
    def capture_execution(self, thread_id):
        # Called under engine.lock when the request is registered/located.
        row = self.repo.conn.execute('SELECT generation FROM workflow_generations WHERE thread_id=?', (thread_id,)).fetchone()
        return WorkflowExecution(self, thread_id, self.context_epoch, row[0] if row else 0)

    def check_request(self, request_id):
        if self.repo.conn.execute("SELECT 1 FROM deletion_jobs WHERE state='business_pending' LIMIT 1").fetchone():
            raise WorkflowConflict(code='workflow_context_changed')
        if self.repo.conn.execute('SELECT 1 FROM request_tombstones WHERE request_id=?', (request_id,)).fetchone():
            raise RequestErased()
        if self.repo.conn.execute('SELECT 1 FROM expired_requests WHERE request_id=?', (request_id,)).fetchone():
            raise WorkflowConflict(code='workflow_expired')
        existing = self.repo.conn.execute(
            'SELECT id FROM workflows WHERE request_id=? UNION SELECT thread_id FROM receipts WHERE request_id=?',
            (request_id, request_id)).fetchone()
        if existing:
            self.check_context_deadline(existing[0])

    def check_execution(self, execution=None):
        check_budget()
        from ..jobs.context import check_job
        check_job()
        execution = execution or _execution.get()
        if execution is None or execution.engine is not self:
            return
        if self.repo.conn.execute("SELECT 1 FROM deletion_jobs WHERE state='business_pending' LIMIT 1").fetchone():
            raise WorkflowConflict(code='workflow_context_changed')
        if execution.epoch != self.context_epoch:
            raise WorkflowConflict(code='workflow_context_changed')
        row = self.repo.conn.execute('SELECT generation FROM workflow_generations WHERE thread_id=?', (execution.thread_id,)).fetchone()
        if row and row[0] != execution.generation:
            raise WorkflowConflict(code='workflow_context_changed')
        workflow = self.repo.workflow(execution.thread_id)
        if not workflow or workflow['status'] in ('cancelled', 'expired'):
            raise WorkflowConflict(code='workflow_context_changed')
        self.check_context_deadline(execution.thread_id)

    def check_context_deadline(self, thread_id):
        from datetime import datetime
        from ..common.clock import now
        row = self.repo.conn.execute('SELECT expires_at FROM workflow_activity WHERE thread_id=?', (thread_id,)).fetchone()
        if row and datetime.fromisoformat(row[0]) <= now():
            raise WorkflowConflict(code='workflow_expired')

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

    def guarded_node(self, operation, node_name=None):
        @wraps(operation)
        def guarded(state):
            from ..observability.context import bind
            from ..observability.spans import node_span
            with bind(self.observability), self.lock, node_span(node_name or operation.__name__):
                self.check_execution()
                token = _node_engine.set(self)
                try:
                    with capture(self.repo, self.repo.workflow_node(state['thread_id'])):
                        if operation.__name__ in {
                            'parse_initial', 'patch_pending', 'prepare_nutrition', 'save_ready',
                            'prepare_clarification', 'persist', 'fact', 'respond',
                            'prepare_plan', 'activate_plan',
                        }:
                            if operation.__name__ != 'activate_plan' or state.get('approved'):
                                self.require_safety(state, operation.__name__)
                        result = operation(state)
                        if operation.__name__ in ('respond', 'prepare_clarification'):
                            self.require_safety(state, operation.__name__)
                        return result
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
            raise RuntimeError('model I/O outside guarded node')
        self.check_execution()
        if self.accounts is not None:
            self.accounts.require_model(self.user_id)
        # Processing plans contain only current input, not mutable health history.
        reads = current_reads(self.repo)
        if reads:
            reads = ReadSet(self.repo, sources=set(reads.sources), scopes=dict(reads.scopes))
        version = self.repo.read_stamp(reads) if reads else self._model_context_version()
        if self.repo.conn.in_transaction or self.cp_conn.in_transaction:
            raise RuntimeError('model I/O inside database transaction')
        self.lock.release()
        try:
            with self.usage.scope('user:' + self.user_id):
                yield
        finally:
            self.lock.acquire()
            self.check_execution()
            current = self.repo.read_stamp(reads) if reads else self._model_context_version()
            if version != current:
                raise WorkflowConflict(code='workflow_context_changed')
