"""Composition root for the application's LangGraph workflows."""

import sqlite3

from ..infrastructure.repositories.repository import Repository
from ..modeling.parser import Parser
from ..privacy.deletion import PrivacyDeletionService
from .chat_flow import ChatFlowMixin
from .chat_parsing import ChatParsingMixin
from .chat_response import ChatResponseMixin
from .concurrency import GuardedSqliteSaver, KeyedLocks, WorkflowConcurrencyMixin
from .planning import PlanningWorkflowMixin
from .policy import PlanHarness
from .runtime import WorkflowRuntimeMixin
from .safety import SafetyWorkflowMixin
from ..common.execution_budget import BudgetLock, RuntimeLimiter
from ..infrastructure.budget_connection import BudgetConnection
from ..infrastructure.data_owner import DataOwner


class Engine(
    SafetyWorkflowMixin,
    ChatParsingMixin,
    ChatFlowMixin,
    ChatResponseMixin,
    PlanningWorkflowMixin,
    WorkflowConcurrencyMixin,
    WorkflowRuntimeMixin,
):
    """Compose focused workflow capabilities behind one runtime object."""

    def __init__(self, settings, services=None, *, user_id):
        from uuid import UUID
        if str(UUID(user_id)) != user_id:
            raise ValueError("invalid_internal_user_id")
        self.user_id = user_id
        self.owner = DataOwner(settings.data_dir)
        self._closed = False
        self.settings = settings
        self._owns_services = services is None
        self.accounts = getattr(services, 'accounts', None) if services else None
        try:
            from ..observability.service import Observability
            self.observability = services.observability if services else Observability(settings)
            self.runtime = services.runtime if services else RuntimeLimiter(settings)
            from ..common.usage import UsageController
            self.usage = services.usage if services else UsageController(settings)
            self.repo = Repository(settings.data_dir / 'health.db')
            self.repo.quality_default_mode = 'local_only' if settings.local_only else 'model_allowed' if settings.mode == 'model' else 'demo'
            with self.repo.conn:
                self.repo.quality_prune()
            self.repo.conn.observer = self.observability
            self.repo.lock.observer = self.observability
            self.repo.context_retention = {
                'pending': settings.context_pending_days, 'plan': settings.context_plan_days,
                'completed': settings.context_completed_days,
            }
            self.lock = BudgetLock(role='engine', observer=self.observability)
            self.execution_locks = KeyedLocks()
            self.context_epoch = 0
            self.cp_conn = sqlite3.connect(
                settings.data_dir / 'checkpoints.db', check_same_thread=False, factory=BudgetConnection
            )
            self.cp_conn.execute('PRAGMA journal_mode=WAL')
            self.cp_conn.execute('PRAGMA secure_delete=ON')
            self.cp_conn.database_role = 'checkpoint'
            self.cp_conn.observer = self.observability
            self.checkpointer = GuardedSqliteSaver(self.cp_conn, self)
            self.checkpointer.setup()
            self.deletions = PrivacyDeletionService(self)
            self.deletions.inventory_checkpoints()
            self.deletions.recover()
            self.repo.recover_resume_attempts()
            self.recover_context_expirations()
            self.repo.recover_jobs()
            self.parser = Parser(settings, network_context=self.model_io)
            self.harness = PlanHarness(self.repo, self.parser)
            self.chat_graph = self.build_chat()
            self.plan_graph = self.build_plan()
            self.analysis_graph = self.build_analysis()

            from ..jobs.runner import JobRunner
            self.jobs = JobRunner(self)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self._closed:
            return
        if hasattr(self, 'jobs'):
            self.jobs.close()
        self._closed = True
        try:
            if self._owns_services and hasattr(self, 'runtime'):
                self.runtime.close()
            if hasattr(self, 'cp_conn'):
                self.cp_conn.close()
            if hasattr(self, 'repo'):
                self.repo.close()
        finally:
            if self._owns_services and hasattr(self, 'observability'):
                self.observability.close()
            self.owner.close()


__all__ = ['Engine']
