"""MemoryService - composed from focused mixins."""

from ....core.config import settings
from ._consolidation import _MemoryConsolidationMixin
from ._crud import _MemoryCrudMixin
from ._decay_sync import _MemoryDecaySyncMixin
from ._extraction import _MemoryExtractionMixin
from ._profile import _MemoryProfileMixin
from ._search import _MemorySearchMixin


class MemoryService(
    _MemoryCrudMixin,
    _MemorySearchMixin,
    _MemoryExtractionMixin,
    _MemoryConsolidationMixin,
    _MemoryProfileMixin,
    _MemoryDecaySyncMixin,
):
    """Long-term user memory with importance, TTL, semantic search, and auto-decay."""

    SEMANTIC_WEIGHT = settings.MEMORY_SCORING_SEMANTIC_WEIGHT
    DECAY_WEIGHT = settings.MEMORY_SCORING_DECAY_WEIGHT
    IMPORTANCE_WEIGHT = settings.MEMORY_SCORING_IMPORTANCE_WEIGHT


__all__ = ["MemoryService"]
