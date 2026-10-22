"""External-model authorization, payload contracts and safe projections."""

from .boundary import ModelBoundary
from .errors import PolicyError
from .projections import record_projection

__all__ = ['ModelBoundary', 'PolicyError', 'record_projection']
