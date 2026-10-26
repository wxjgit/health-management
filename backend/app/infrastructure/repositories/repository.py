"""Composition root for the SQLite repository capabilities."""

from .common import RepositoryBase
from .events import EventRepositoryMixin
from .maintenance import MaintenanceRepositoryMixin
from .planning import PlanningRepositoryMixin
from .provenance import ProvenanceRepositoryMixin
from .workflows import WorkflowRepositoryMixin


class Repository(
    ProvenanceRepositoryMixin,
    EventRepositoryMixin,
    WorkflowRepositoryMixin,
    PlanningRepositoryMixin,
    MaintenanceRepositoryMixin,
    RepositoryBase,
):
    pass


__all__ = ['Repository']
