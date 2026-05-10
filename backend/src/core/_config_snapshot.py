"""Immutable settings snapshot for request-scoped config stability."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SettingsSnapshot:
    """Immutable runtime snapshot used to keep a request on one settings version."""

    epoch: int
    llm_binding: str
    llm_model: str
    embedding_binding: str
    embedding_model: str
    embedding_dimension: int
    retriever_provider: str
    raganything_enabled: bool
    raganything_fallback_to_native: bool
    hybrid_search_enabled: bool
    hybrid_alpha: float
    top_k: int
    score_threshold: float


def build_settings_snapshot(*, epoch: int) -> SettingsSnapshot:
    """Capture a stable subset of runtime settings for one pipeline execution."""
    from .config import settings

    return SettingsSnapshot(
        epoch=epoch,
        llm_binding=settings.LLM_BINDING,
        llm_model=settings.LLM_MODEL,
        embedding_binding=settings.EMBEDDING_BINDING,
        embedding_model=settings.EMBEDDING_MODEL,
        embedding_dimension=settings.EMBEDDING_DIMENSION,
        retriever_provider=settings.RAG_RETRIEVER_PROVIDER,
        raganything_enabled=settings.RAGANYTHING_ENABLED,
        raganything_fallback_to_native=settings.RAGANYTHING_FALLBACK_TO_NATIVE,
        hybrid_search_enabled=settings.HYBRID_SEARCH_ENABLED,
        hybrid_alpha=settings.HYBRID_ALPHA,
        top_k=settings.TOP_K,
        score_threshold=settings.SCORE_THRESHOLD,
    )
