"""Composition root for the application's LangGraph workflows."""

import sqlite3
from threading import RLock

from ..infrastructure.repositories.repository import Repository
from ..modeling.parser import Parser
from .chat_flow import ChatFlowMixin
from .chat_parsing import ChatParsingMixin
from .chat_response import ChatResponseMixin
from .concurrency import GuardedSqliteSaver, KeyedLocks, WorkflowConcurrencyMixin
from .planning import PlanningWorkflowMixin
from .policy import PlanHarness
from .runtime import WorkflowRuntimeMixin


class Engine(
    ChatParsingMixin,
    ChatFlowMixin,
    ChatResponseMixin,
    PlanningWorkflowMixin,
    WorkflowConcurrencyMixin,
    WorkflowRuntimeMixin,
):
    """Compose focused workflow capabilities behind one runtime object."""

    def __init__(self, settings):
        self.settings = settings
        self.repo = Repository(settings.data_dir / 'health.db')
        self.lock = RLock()
        self.execution_locks = KeyedLocks()
        self.context_epoch = 0
        self.cp_conn = sqlite3.connect(
            settings.data_dir / 'checkpoints.db', check_same_thread=False
        )
        self.cp_conn.execute('PRAGMA journal_mode=WAL')
        self.checkpointer = GuardedSqliteSaver(self.cp_conn, self)
        self.checkpointer.setup()
        self.parser = Parser(settings, network_context=self.model_io)
        self.harness = PlanHarness(self.repo, self.parser)
        self.chat_graph = self.build_chat()
        self.plan_graph = self.build_plan()
        self.analysis_graph = self.build_analysis()

    def close(self):
        self.cp_conn.close()
        self.repo.close()


__all__ = ['Engine']
