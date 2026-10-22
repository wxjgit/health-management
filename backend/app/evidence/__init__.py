"""Evidence binding, correction and verification services."""

from .binding import ground_event, ground_patch
from .core import GroundingError
from .corrections import ground_correction, merge_patch_evidence
from .verification import assert_grounded

__all__ = [
    'GroundingError',
    'assert_grounded',
    'ground_correction',
    'ground_event',
    'ground_patch',
    'merge_patch_evidence',
]
