"""Health-domain queries and use cases."""

from .analysis import analyze, meal_assessment, review
from .dashboard import dashboard, evidence, history_window, recommendation
from .planning import plan_draft, suggest_weights
from .retention import archive

__all__ = [
    'analyze',
    'archive',
    'dashboard',
    'evidence',
    'history_window',
    'meal_assessment',
    'plan_draft',
    'recommendation',
    'review',
    'suggest_weights',
]
