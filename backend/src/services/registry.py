"""Registry for runtime-resettable singleton services."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)


class ResettableService(Protocol):
    """Protocol for singleton services that can be reset safely."""

    name: str

    def reset(self) -> None: ...


@dataclass(frozen=True)
class _FunctionResettable:
    name: str
    reset_fn: Callable[[], None]

    def reset(self) -> None:
        self.reset_fn()


_registry_lock = threading.Lock()
_registry: dict[str, ResettableService] = {}
_defaults_registered = False


def register_resettable(service: ResettableService) -> None:
    """Register or replace a resettable service by name."""
    with _registry_lock:
        _registry[service.name] = service


def register_resettable_fn(name: str, reset_fn: Callable[[], None]) -> None:
    """Register a plain reset function as a resettable service."""
    register_resettable(_FunctionResettable(name=name, reset_fn=reset_fn))  # type: ignore[arg-type]


def list_resettable_names() -> list[str]:
    with _registry_lock:
        return sorted(_registry.keys())


def reset_all_services() -> list[str]:
    """Reset all registered services and return names in reset order."""
    with _registry_lock:
        services = list(_registry.values())
    reset_names: list[str] = []
    for service in services:
        service.reset()
        reset_names.append(service.name)
    return reset_names


def _reset_llm_cache_and_traffic_controller() -> None:
    from .llm import _get_llm_cache
    from .traffic_control import init_traffic_controller

    cache = _get_llm_cache()
    if cache:
        cache.clear()
    init_traffic_controller()


def initialize_default_resettable_services() -> None:
    """Register built-in resettable services once."""
    global _defaults_registered
    with _registry_lock:
        if _defaults_registered:
            return
        _defaults_registered = True

    from .chain._skill_matching import reset_skill_loader, reset_skill_matcher
    from .embedder import reset_embeddings
    from .knowledge_graph._vectorstore import reset_entity_vectorstore
    from .llm import reset_extraction_llm, reset_llm
    from .memory._summary_vectorstore import (
        reset_summary_vectorstore as reset_session_summary_store,
    )
    from .memory._vectorstore import reset_memory_vectorstore
    from .rag import reset_reranker_state, reset_rewrite_llm, reset_vectorstore
    from .rag._meeting_summary_vectorstore import reset_meeting_summary_vectorstore
    from .rag._raganything import reset_raganything
    from .rag._summary_vectorstore import reset_summary_vectorstore as reset_file_summary_store

    register_resettable_fn("llm", reset_llm)
    register_resettable_fn("extraction_llm", reset_extraction_llm)
    register_resettable_fn("embeddings", reset_embeddings)
    register_resettable_fn("memory_vectorstore", reset_memory_vectorstore)
    register_resettable_fn("session_summary_vectorstore", reset_session_summary_store)
    register_resettable_fn("entity_vectorstore", reset_entity_vectorstore)
    register_resettable_fn("file_summary_vectorstore", reset_file_summary_store)
    register_resettable_fn("meeting_summary_vectorstore", reset_meeting_summary_vectorstore)
    register_resettable_fn("raganything", reset_raganything)
    register_resettable_fn("reranker", reset_reranker_state)
    register_resettable_fn("query_rewrite", reset_rewrite_llm)
    register_resettable_fn("vectorstore", reset_vectorstore)
    register_resettable_fn("llm_cache+traffic", _reset_llm_cache_and_traffic_controller)
    register_resettable_fn("skill_loader", reset_skill_loader)
    register_resettable_fn("skill_matcher", reset_skill_matcher)

    # H-11: Clear resolver L1 cache on settings change so stale rewrites
    # don't persist across model/config changes.
    def _reset_resolver_cache() -> None:
        from .chain._resolver import clear_l1_cache

        clear_l1_cache()

    register_resettable_fn("resolver_cache", _reset_resolver_cache)

    def _reset_file_summary_cache() -> None:
        from .chain._generate_helpers import _file_summary_cache, _file_summary_cache_lock

        with _file_summary_cache_lock:
            _file_summary_cache.clear()

    register_resettable_fn("file_summary_cache", _reset_file_summary_cache)

    def _reset_stream_semaphore() -> None:
        from .concurrency import reset_stream_semaphore

        reset_stream_semaphore()

    register_resettable_fn("stream_semaphore", _reset_stream_semaphore)
    logger.debug("Registered default resettable services: %s", ", ".join(list_resettable_names()))
