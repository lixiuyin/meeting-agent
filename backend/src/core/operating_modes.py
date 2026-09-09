"""Stable public operating modes for RAG and long-term memory."""

from typing import Literal

type RetrievalProfile = Literal["fast", "balanced", "thorough"]
type MemoryMode = Literal["off", "focused", "balanced", "deep"]

RETRIEVAL_PROFILES: tuple[RetrievalProfile, ...] = ("fast", "balanced", "thorough")
MEMORY_MODES: tuple[MemoryMode, ...] = ("off", "focused", "balanced", "deep")

DEFAULT_RETRIEVAL_PROFILE: RetrievalProfile = "balanced"
DEFAULT_MEMORY_MODE: MemoryMode = "balanced"
