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

    @property
    def SEMANTIC_WEIGHT(self) -> float:
        return float(settings.MEMORY_SCORING_SEMANTIC_WEIGHT)

    @property
    def DECAY_WEIGHT(self) -> float:
        return float(settings.MEMORY_SCORING_DECAY_WEIGHT)

    @property
    def IMPORTANCE_WEIGHT(self) -> float:
        return float(settings.MEMORY_SCORING_IMPORTANCE_WEIGHT)

    @property
    def CONFIDENCE_WEIGHT(self) -> float:
        return float(settings.MEMORY_SCORING_CONFIDENCE_WEIGHT)

    @property
    def USEFULNESS_WEIGHT(self) -> float:
        return float(settings.MEMORY_SCORING_USEFULNESS_WEIGHT)


__all__ = ["MemoryService"]
