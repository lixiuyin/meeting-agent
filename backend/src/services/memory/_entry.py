from dataclasses import dataclass, field


@dataclass
class MemoryEntry:
    """A retrieved memory with its computed relevance score."""

    key: str
    value: str
    importance: int
    category: str | None
    source: str
    last_accessed: str | None
    access_count: int
    expires_at: str | None
    updated_at: str
    # Scope metadata (from vector store, None = global/unscoped)
    meeting_ids: list[int] | None = field(default=None, repr=False)
    file_ids: list[int] | None = field(default=None, repr=False)
    # Flag set by migration v29 for memories created before scope support.
    # When True, the entry is excluded from scoped queries (only visible to
    # unscoped queries) to prevent cross-meeting pollution from legacy data.
    is_legacy_scope: bool = field(default=False, repr=False)
    # Computed fields
    decay_score: float = 0.0
    semantic_score: float = 0.0
    combined_score: float = 0.0
