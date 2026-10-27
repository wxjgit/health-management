"""Composition root for the SQLite repository capabilities."""

from .common import RepositoryBase
from .jobs import JobsRepositoryMixin
from .outbox import OutboxRepositoryMixin
from .observability import ObservabilityRepositoryMixin
from .events import EventRepositoryMixin
from .facts import FactRepositoryMixin
from .maintenance import MaintenanceRepositoryMixin
from .planning import PlanningRepositoryMixin
from .provenance import ProvenanceRepositoryMixin
from .workflows import WorkflowRepositoryMixin


class Repository(
    ObservabilityRepositoryMixin,
    JobsRepositoryMixin,
    OutboxRepositoryMixin,
    ProvenanceRepositoryMixin,
    EventRepositoryMixin,
    FactRepositoryMixin,
    WorkflowRepositoryMixin,
    PlanningRepositoryMixin,
    MaintenanceRepositoryMixin,
    RepositoryBase,
):
    pass


__all__ = ['Repository']
