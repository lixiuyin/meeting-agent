"""Immutable request-scoped settings snapshots."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass
from threading import RLock
from types import MappingProxyType
from typing import Any

from .operating_modes import MEMORY_MODES, RETRIEVAL_PROFILES, MemoryMode, RetrievalProfile

_active_snapshot: ContextVar[Any] = ContextVar("meeting_agent_settings_snapshot", default=None)
_staged_settings: ContextVar[Any] = ContextVar("meeting_agent_staged_settings", default=None)


def submit_with_context(executor: Any, func: Any, /, *args: Any, **kwargs: Any) -> Any:
    """Submit work while preserving request-scoped ContextVar values."""
    context = copy_context()
    return executor.submit(context.run, func, *args, **kwargs)


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class SettingsSnapshot:
    """Complete immutable configuration captured at request admission."""

    epoch: int
    values: Mapping[str, Any]

    def get(self, name: str, default: Any = None) -> Any:
        return self.values.get(name, default)

    def __getattr__(self, name: str) -> Any:
        # Preserve the original lower-case convenience attributes while also
        # allowing exact Settings field names for new consumers.
        aliases = {
            "llm_binding": "LLM_BINDING",
            "llm_model": "LLM_MODEL",
            "embedding_binding": "EMBEDDING_BINDING",
            "embedding_model": "EMBEDDING_MODEL",
            "embedding_dimension": "EMBEDDING_DIMENSION",
            "retriever_provider": "RAG_RETRIEVER_PROVIDER",
            "raganything_enabled": "RAGANYTHING_ENABLED",
            "raganything_fallback_to_native": "RAGANYTHING_FALLBACK_TO_NATIVE",
            "hybrid_search_enabled": "HYBRID_SEARCH_ENABLED",
            "hybrid_alpha": "HYBRID_ALPHA",
            "top_k": "TOP_K",
            "score_threshold": "SCORE_THRESHOLD",
        }
        key = aliases.get(name, name)
        try:
            return self.values[key]
        except KeyError as exc:
            raise AttributeError(name) from exc


class ContextAwareSettings:
    """Delegate to live Settings except while a request snapshot is active."""

    _live: Any
    _swap_lock: Any
    __slots__ = ("_live", "_swap_lock")

    def __init__(self, live: Any) -> None:
        object.__setattr__(self, "_live", live)
        object.__setattr__(self, "_swap_lock", RLock())

    def __getattr__(self, name: str) -> Any:
        snapshot = _active_snapshot.get()
        if snapshot is not None and name in snapshot.values:
            return snapshot.values[name]
        with self._swap_lock:
            live = self._live
        return getattr(live, name)

    def __setattr__(self, name: str, value: Any) -> None:
        staged = _staged_settings.get()
        if staged is not None:
            setattr(staged, name, value)
            return
        with self._swap_lock:
            setattr(self._live, name, value)

    def copy_live(self) -> Any:
        """Return a deep candidate copy without exposing a partially updated live object."""
        with self._swap_lock:
            return self._live.model_copy(deep=True)

    def replace_live(self, replacement: Any) -> Any:
        """Atomically publish a fully validated Settings instance."""
        with self._swap_lock:
            previous = self._live
            object.__setattr__(self, "_live", replacement)
            return previous

    def snapshot_values(self, field_names: Any) -> dict[str, Any]:
        """Capture a coherent set of fields from one live Settings generation."""
        with self._swap_lock:
            live = self._live
            return {name: _freeze(getattr(live, name)) for name in field_names}

    @contextmanager
    def stage_updates(self, candidate: Any):
        """Redirect legacy attribute assignments into an unpublished candidate."""
        token = _staged_settings.set(candidate)
        try:
            yield candidate
        finally:
            _staged_settings.reset(token)


@contextmanager
def activate_settings_snapshot(snapshot: SettingsSnapshot | None):
    """Pin all global settings reads in the current async context."""
    if snapshot is None:
        yield
        return
    token = _active_snapshot.set(snapshot)
    try:
        yield
    finally:
        _active_snapshot.reset(token)


def build_settings_snapshot(
    *, epoch: int, overrides: Mapping[str, Any] | None = None
) -> SettingsSnapshot:
    """Capture every user-configurable value for one pipeline execution."""
    from .config import Settings, settings

    values = settings.snapshot_values(Settings.model_fields)
    for name, value in (overrides or {}).items():
        if name not in values:
            raise KeyError(f"Unknown settings override: {name}")
        values[name] = _freeze(value)
    return SettingsSnapshot(epoch=epoch, values=MappingProxyType(values))


def build_retrieval_profile_snapshot(
    *,
    epoch: int,
    profile: RetrievalProfile,
    memory_mode: MemoryMode = "balanced",
) -> SettingsSnapshot:
    """Translate stable public RAG and memory modes into request-local settings.

    These modes are the supported operational interface. The underlying
    settings remain available for deployment tuning, but callers should not
    need to coordinate a collection of thresholds for each request.
    """
    if profile not in RETRIEVAL_PROFILES:
        raise ValueError(f"Unknown retrieval profile: {profile!r}")
    if memory_mode not in MEMORY_MODES:
        raise ValueError(f"Unknown memory mode: {memory_mode!r}")

    # Capture one complete configuration generation before deriving any preset.
    # Reading the live proxy field-by-field here could otherwise mix values from
    # two generations when the settings API publishes a reload concurrently.
    base = build_settings_snapshot(epoch=epoch)
    overrides: dict[str, Any] = {}
    if profile == "fast":
        overrides.update(
            {
                "TOP_K": min(base.TOP_K, 5),
                "QUERY_REWRITE_ENABLED": False,
                "MULTI_QUERY_ENABLED": False,
                "RERANKER_BINDING": "",
                "RAG_RERANK_FETCH_MULTIPLIER": 1,
            }
        )
    elif profile == "thorough":
        top_k = max(base.TOP_K, 16)
        overrides.update(
            {
                "TOP_K": top_k,
                "QUERY_REWRITE_ENABLED": True,
                "MULTI_QUERY_ENABLED": True,
                "RERANKER_TOP_N": max(base.RERANKER_TOP_N, top_k),
                "RAG_RERANK_FETCH_MULTIPLIER": max(base.RAG_RERANK_FETCH_MULTIPLIER, 6),
            }
        )

    if memory_mode == "off":
        overrides.update(
            {
                "MEMORY_AUTO_EXTRACT": False,
                "MEMORY_MAX_CONTEXT_ITEMS": 0,
                "GLOBAL_MEMORY_LIMIT": 0,
                "MEMORY_MULTI_HOP_ENABLED": False,
                "KNOWLEDGE_GRAPH_ENABLED": False,
                "MEMORY_PROFILE_ENABLED": False,
                "SESSION_SUMMARY_ENABLED": False,
            }
        )
    elif memory_mode == "focused":
        overrides.update(
            {
                "MEMORY_AUTO_EXTRACT": True,
                "MEMORY_MAX_FACTS_PER_TURN": min(base.MEMORY_MAX_FACTS_PER_TURN, 2),
                "MEMORY_MAX_CONTEXT_ITEMS": min(base.MEMORY_MAX_CONTEXT_ITEMS, 3),
                "GLOBAL_MEMORY_LIMIT": min(base.GLOBAL_MEMORY_LIMIT, 1),
                "MEMORY_EXTRACTION_MODE": "precise",
                "MEMORY_MULTI_HOP_ENABLED": False,
                "KNOWLEDGE_GRAPH_ENABLED": False,
            }
        )
    elif memory_mode == "deep":
        overrides.update(
            {
                "MEMORY_AUTO_EXTRACT": True,
                "MEMORY_MAX_FACTS_PER_TURN": max(base.MEMORY_MAX_FACTS_PER_TURN, 5),
                "MEMORY_MAX_CONTEXT_ITEMS": max(base.MEMORY_MAX_CONTEXT_ITEMS, 8),
                "GLOBAL_MEMORY_LIMIT": max(base.GLOBAL_MEMORY_LIMIT, 5),
                "MEMORY_EXTRACTION_MODE": "aggressive",
                "MEMORY_MULTI_HOP_ENABLED": True,
                "KNOWLEDGE_GRAPH_ENABLED": True,
                "MEMORY_PROFILE_ENABLED": True,
                "SESSION_SUMMARY_ENABLED": True,
            }
        )

    values = dict(base.values)
    values.update({name: _freeze(value) for name, value in overrides.items()})
    return SettingsSnapshot(epoch=epoch, values=MappingProxyType(values))
