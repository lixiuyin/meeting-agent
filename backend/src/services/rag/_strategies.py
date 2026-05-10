"""Retrieval strategy protocol and selector."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from ...core.trace import TraceContext


class RetrievalStrategy(Protocol):
    """Strategy contract for retrieval provider execution."""

    @property
    def name(self) -> str: ...

    def retrieve(
        self,
        *,
        query: str,
        filters: dict,
        k: int,
        top_k: int,
        threshold: float,
        scoped: bool,
        trace: TraceContext | None,
    ) -> list[dict]: ...


@dataclass(frozen=True)
class NativeStrategy:
    name: str
    run: Callable[..., list[dict]]

    def retrieve(
        self,
        *,
        query: str,
        filters: dict,
        k: int,
        top_k: int,
        threshold: float,
        scoped: bool,
        trace: TraceContext | None,
    ) -> list[dict]:
        return self.run(
            query=query,
            filters=filters,
            k=k,
            top_k=top_k,
            threshold=threshold,
            trace=trace,
        )


@dataclass(frozen=True)
class HybridStrategy:
    name: str
    run: Callable[..., list[dict]]

    def retrieve(
        self,
        *,
        query: str,
        filters: dict,
        k: int,
        top_k: int,
        threshold: float,
        scoped: bool,
        trace: TraceContext | None,
    ) -> list[dict]:
        return self.run(
            query=query,
            filters=filters,
            k=k,
            top_k=top_k,
            threshold=threshold,
            trace=trace,
        )


@dataclass(frozen=True)
class MultimodalStrategy:
    name: str
    run: Callable[..., list[dict]]

    def retrieve(
        self,
        *,
        query: str,
        filters: dict,
        k: int,
        top_k: int,
        threshold: float,
        scoped: bool,
        trace: TraceContext | None,
    ) -> list[dict]:
        return self.run(
            query=query,
            filters=filters,
            k=k,
            top_k=top_k,
            threshold=threshold,
            scoped=scoped,
            trace=trace,
        )


@dataclass(frozen=True)
class HybridMultimodalStrategy:
    name: str
    run: Callable[..., list[dict]]

    def retrieve(
        self,
        *,
        query: str,
        filters: dict,
        k: int,
        top_k: int,
        threshold: float,
        scoped: bool,
        trace: TraceContext | None,
    ) -> list[dict]:
        return self.run(
            query=query,
            filters=filters,
            k=k,
            top_k=top_k,
            threshold=threshold,
            scoped=scoped,
            trace=trace,
        )


def select_strategy(
    provider: str,
    *,
    native: NativeStrategy,
    hybrid: HybridStrategy,
    multimodal: MultimodalStrategy,
    hybrid_multimodal: HybridMultimodalStrategy,
) -> RetrievalStrategy:
    """Select retrieval strategy by normalized provider."""
    if provider == "hybrid":
        return hybrid
    if provider == "multimodal":
        return multimodal
    if provider == "hybrid_multimodal":
        return hybrid_multimodal
    return native
