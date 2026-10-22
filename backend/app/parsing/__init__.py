"""Local deterministic parsing used by demo and local-only workflows."""

from .demo import NUMBER, demo_parse, duration, number

__all__ = ['NUMBER', 'demo_parse', 'duration', 'number']
