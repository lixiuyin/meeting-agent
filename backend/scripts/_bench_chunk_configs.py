"""Chunk configuration presets for benchmark Phase 1."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ChunkConfig:
    """A single benchmark chunk configuration."""

    name: str
    preset: str
    method: str  # "native", "flat", "parent_child"
    chunk_size: int
    chunk_overlap: int
    parent_child_enabled: bool
    child_chunk_size: int | None = None
    child_chunk_overlap: int | None = None
    audio_semantic_boundary_enabled: bool | None = None
    audio_semantic_boundary_threshold: float | None = None
    non_text_chunking_strategy: str = "native"  # "native" or "text"


# Audio modality configs per benchmark plan §2.5
AUDIO_CHUNK_CONFIGS: list[ChunkConfig] = [
    # A — Native Segment-Aware
    ChunkConfig(
        name="A Native（Segment-Aware）",
        preset="S",
        method="native",
        chunk_size=512,
        chunk_overlap=64,
        parent_child_enabled=False,
        audio_semantic_boundary_enabled=True,
        audio_semantic_boundary_threshold=0.5,
    ),
    ChunkConfig(
        name="A Native（Segment-Aware）",
        preset="M",
        method="native",
        chunk_size=1024,
        chunk_overlap=128,
        parent_child_enabled=False,
        audio_semantic_boundary_enabled=True,
        audio_semantic_boundary_threshold=0.5,
    ),
    ChunkConfig(
        name="A Native（Segment-Aware）",
        preset="L",
        method="native",
        chunk_size=2048,
        chunk_overlap=256,
        parent_child_enabled=False,
        audio_semantic_boundary_enabled=False,
        audio_semantic_boundary_threshold=None,
    ),
    # B — Flat
    ChunkConfig(
        name="B Flat",
        preset="S",
        method="flat",
        chunk_size=512,
        chunk_overlap=64,
        parent_child_enabled=False,
        non_text_chunking_strategy="text",
    ),
    ChunkConfig(
        name="B Flat",
        preset="M",
        method="flat",
        chunk_size=1024,
        chunk_overlap=128,
        parent_child_enabled=False,
        non_text_chunking_strategy="text",
    ),
    ChunkConfig(
        name="B Flat",
        preset="L",
        method="flat",
        chunk_size=2048,
        chunk_overlap=256,
        parent_child_enabled=False,
        non_text_chunking_strategy="text",
    ),
    # C — Parent-Child
    ChunkConfig(
        name="C Parent-Child",
        preset="S",
        method="parent_child",
        chunk_size=1024,
        chunk_overlap=128,
        parent_child_enabled=True,
        child_chunk_size=256,
        child_chunk_overlap=32,
        non_text_chunking_strategy="text",
    ),
    ChunkConfig(
        name="C Parent-Child",
        preset="L",
        method="parent_child",
        chunk_size=2048,
        chunk_overlap=256,
        parent_child_enabled=True,
        child_chunk_size=512,
        child_chunk_overlap=64,
        non_text_chunking_strategy="text",
    ),
]


def apply_chunk_config(cfg: ChunkConfig) -> None:
    """Apply a chunk config to global settings."""
    from src.core.config import settings

    settings.CHUNK_SIZE = cfg.chunk_size
    settings.CHUNK_OVERLAP = cfg.chunk_overlap
    settings.PARENT_CHILD_ENABLED = cfg.parent_child_enabled

    if cfg.child_chunk_size is not None:
        settings.CHILD_CHUNK_SIZE = cfg.child_chunk_size
    if cfg.child_chunk_overlap is not None:
        settings.CHILD_CHUNK_OVERLAP = cfg.child_chunk_overlap

    if cfg.audio_semantic_boundary_enabled is not None:
        settings.AUDIO_SEMANTIC_BOUNDARY_ENABLED = cfg.audio_semantic_boundary_enabled
    if cfg.audio_semantic_boundary_threshold is not None:
        settings.AUDIO_SEMANTIC_BOUNDARY_THRESHOLD = cfg.audio_semantic_boundary_threshold

    # Apply non-text strategy so native artefacts are routed to text chunking when needed
    settings.NON_TEXT_CHUNKING_STRATEGY = cfg.non_text_chunking_strategy


def apply_non_text_strategy(strategy: str) -> None:
    """Switch NON_TEXT_CHUNKING_STRATEGY between 'native' and 'text'."""
    from src.core.config import settings

    settings.NON_TEXT_CHUNKING_STRATEGY = strategy


def apply_retrieval_config(
    provider: str,
    reranker_binding: str = "",
) -> None:
    """Apply retrieval strategy config."""
    from src.core.config import settings

    settings.RAG_RETRIEVER_PROVIDER = provider
    settings.RERANKER_BINDING = reranker_binding


def lock_benchmark_settings() -> None:
    """Lock shared settings for fair comparison across benchmark runs."""
    from src.core.config import settings

    settings.TOP_K = 10
    settings.RERANKER_TOP_N = 10
    settings.HYBRID_ALPHA = 0.5
    settings.RAG_RERANK_FETCH_MULTIPLIER = 6
    settings.RAG_FAIR_ADAPTIVE_CHUNKS = True
    settings.RAG_FILE_SCOPING_MODE = "router_and_funnel"
    settings.RAG_MEETING_SUMMARY_ROUTER_ENABLED = True
    settings.RAG_BROAD_RECALL_MULTI_QUERY_ENABLED = False
    settings.QUERY_REWRITE_ENABLED = False
    # Phase 1 should run in hybrid mode (vector + BM25)
    settings.HYBRID_SEARCH_ENABLED = True
    # Enable embedding query cache so repeated golden queries
    # across chunk configs do not trigger duplicate API calls.
    settings.EMBEDDING_QUERY_CACHE_ENABLED = True
    # Extend vector-search timeout so slow embedding providers don't
    # dominate the benchmark with BM25 fallbacks.
    settings.VECTOR_SEARCH_TIMEOUT_S = 60.0


def config_to_dict(cfg: ChunkConfig) -> dict[str, Any]:
    """Serialize config for JSON output."""
    return {
        "name": cfg.name,
        "preset": cfg.preset,
        "method": cfg.method,
        "chunk_size": cfg.chunk_size,
        "chunk_overlap": cfg.chunk_overlap,
        "parent_child_enabled": cfg.parent_child_enabled,
        "child_chunk_size": cfg.child_chunk_size,
        "child_chunk_overlap": cfg.child_chunk_overlap,
        "audio_semantic_boundary_enabled": cfg.audio_semantic_boundary_enabled,
        "audio_semantic_boundary_threshold": cfg.audio_semantic_boundary_threshold,
        "non_text_chunking_strategy": cfg.non_text_chunking_strategy,
    }
