"""Memory-related Pydantic models."""

import json
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator


class _ValidityWindowMixin(BaseModel):
    """Require canonical timezone-aware memory validity windows."""

    @model_validator(mode="after")
    def _valid_window_order(self):
        valid_from = getattr(self, "valid_from", None)
        valid_to = getattr(self, "valid_to", None)
        if valid_from is not None and valid_to is not None and valid_from > valid_to:
            raise ValueError("valid_from must be earlier than or equal to valid_to")
        return self


class MemorySetRequest(_ValidityWindowMixin):
    key: str = Field(..., min_length=1, max_length=200)
    value: str = Field(..., min_length=1, max_length=10000)
    importance: float = Field(3.0, ge=1.0, le=5.0, description="Importance score 1-5")
    category: str | None = Field(None, max_length=100, description="Memory category tag")
    confidence: float = Field(1.0, ge=0.0, le=1.0)
    fact_type: Literal["fact", "preference", "project_fact", "decision", "action_item"] = "fact"
    assertion_status: Literal["pending", "confirmed", "disputed", "superseded", "retracted"] = (
        "confirmed"
    )
    project_id: str | None = Field(None, max_length=200)
    action_status: Literal["open", "in_progress", "blocked", "done", "cancelled"] | None = None
    assignee: str | None = Field(None, max_length=500)
    due_at: AwareDatetime | None = None
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None
    expires_in_days: int | None = Field(
        None, ge=-1, description="TTL in days (-1=never, default 90)"
    )


class MemoryResponse(BaseModel):
    archived_at: str | None = None
    archive_reason: str | None = None
    key: str
    value: str
    source: str
    importance: float = 3.0
    salience: float = 3.0
    confidence: float = 1.0
    freshness_score: float = 1.0
    usefulness_score: float = 0.0
    usefulness_count: int = 0
    category: str | None = None
    access_count: int = 0
    expires_at: str | None = None
    last_accessed: str | None = None
    updated_at: str
    relevance_score: float | None = None
    superseded_by: str | None = None
    session_id: str | None = None
    last_confirmed_at: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    evidence_message_ids: list[int] | None = None
    evidence_excerpt: str | None = None
    evidence_refs: list[dict[str, object]] | None = None
    conflicts_with: list[str] | None = None
    meeting_ids: list[int] | None = None
    file_ids: list[int] | None = None
    is_legacy_scope: bool | None = None
    vector_state: str | None = None
    revision: int = 1
    fact_type: str = "fact"
    assertion_status: str = "confirmed"
    project_id: str | None = None
    subject: str | None = None
    predicate: str | None = None
    object_value: str | None = None
    retracted_at: str | None = None
    action_status: str | None = None
    assignee: str | None = None
    due_at: str | None = None

    @field_validator("evidence_message_ids", "evidence_refs", "conflicts_with", mode="before")
    @classmethod
    def _decode_json_list(cls, value):
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                return None
            return decoded if isinstance(decoded, list) else None
        return value

    @field_validator("meeting_ids", "file_ids", mode="before")
    @classmethod
    def _decode_scope_ids(cls, value):
        if isinstance(value, str):
            return [int(item) for item in value.split(",") if item.strip()]
        return value


class MemoryUpdateRequest(_ValidityWindowMixin):
    key: str = Field(..., min_length=1, max_length=200)
    value: str | None = Field(None, min_length=1, max_length=10000)
    importance: float | None = Field(None, ge=1.0, le=5.0, description="Importance score 1-5")
    category: str | None = Field(None, max_length=100, description="Memory category tag")
    confidence: float | None = Field(None, ge=0.0, le=1.0)
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None
    expected_revision: int = Field(..., ge=1)
    fact_type: Literal["fact", "preference", "project_fact", "decision", "action_item"] | None = (
        None
    )
    assertion_status: (
        Literal["pending", "confirmed", "disputed", "superseded", "retracted"] | None
    ) = None
    project_id: str | None = Field(None, max_length=200)
    action_status: Literal["open", "in_progress", "blocked", "done", "cancelled"] | None = None
    assignee: str | None = Field(None, max_length=500)
    due_at: AwareDatetime | None = None


class MemoryConflictResolveRequest(BaseModel):
    winner_key: str = Field(..., min_length=1, max_length=200)
    expected_revision: int = Field(..., ge=1)
    conflicting_keys: list[str] = Field(..., min_length=1, max_length=100)
    expected_conflict_revisions: dict[str, int] = Field(default_factory=dict, max_length=100)

    @model_validator(mode="after")
    def _validate_distinct_keys(self):
        self.conflicting_keys = list(dict.fromkeys(key.strip() for key in self.conflicting_keys))
        if any(not key or len(key) > 200 for key in self.conflicting_keys):
            raise ValueError("Each conflicting key must contain 1-200 characters")
        if self.winner_key in self.conflicting_keys:
            raise ValueError("winner_key cannot also be a conflicting key")
        return self


class MemoryConflictResolveResponse(BaseModel):
    winner: MemoryResponse
    superseded_keys: list[str]


class MemoryVersionResponse(BaseModel):
    revision: int
    value: str
    source: str
    fact_type: str = "fact"
    assertion_status: str = "confirmed"
    project_id: str | None = None
    subject: str | None = None
    predicate: str | None = None
    object_value: str | None = None
    action_status: str | None = None
    assignee: str | None = None
    due_at: str | None = None
    category: str | None = None
    confidence: float = 1.0
    valid_from: str | None = None
    valid_to: str | None = None
    evidence_message_ids: list[int] | None = None
    evidence_excerpt: str | None = None
    evidence_refs: list[dict[str, object]] | None = None
    conflicts_with: list[str] | None = None
    meeting_ids: list[int] | None = None
    file_ids: list[int] | None = None
    recorded_at: str
    recorded_to: str | None = None

    @field_validator("evidence_message_ids", "evidence_refs", "conflicts_with", mode="before")
    @classmethod
    def _decode_version_json_list(cls, value):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return None
        return value if isinstance(value, list) else None

    @field_validator("meeting_ids", "file_ids", mode="before")
    @classmethod
    def _decode_version_scope(cls, value):
        if isinstance(value, str):
            return [int(item) for item in value.split(",") if item.strip()]
        return value


class MemoryFeedbackRequest(BaseModel):
    key: str = Field(..., min_length=1, max_length=200)
    useful: bool


class MemoryFeedbackResponse(BaseModel):
    message: str
    key: str
    usefulness_score: float = Field(..., ge=0.0, le=1.0)
    usefulness_count: int = Field(..., ge=1)


class MemoryListResponse(BaseModel):
    items: list[MemoryResponse]
    next_cursor: str | None = None
    total: int | None = None
    # Backward compatibility for existing frontend clients.
    memories: list[MemoryResponse] | None = None


class MemorySearchRequest(BaseModel):
    memory_kind: Literal["all", "personal", "reference"] = "all"
    fact_type: Literal["fact", "preference", "project_fact", "decision", "action_item"] | None = (
        None
    )
    assertion_status: (
        Literal["pending", "confirmed", "disputed", "superseded", "retracted"] | None
    ) = None
    project_id: str | None = Field(None, max_length=200)
    query: str = Field(
        ..., min_length=1, max_length=2_000, description="Semantic search query for memories"
    )
    limit: int = Field(5, ge=1, le=20)
    min_importance: float = Field(1.0, ge=1.0, le=5.0)
    meeting_ids: list[int] | None = Field(None, max_length=100)
    file_ids: list[int] | None = Field(None, max_length=100)


class MemoryBatchItem(_ValidityWindowMixin):
    key: str = Field(..., min_length=1, max_length=200)
    value: str = Field(..., min_length=1, max_length=10000)
    importance: float = Field(3.0, ge=1.0, le=5.0)
    category: str | None = Field(None, max_length=100)
    confidence: float = Field(1.0, ge=0.0, le=1.0)
    fact_type: Literal["fact", "preference", "project_fact", "decision", "action_item"] = "fact"
    assertion_status: Literal["pending", "confirmed", "disputed", "superseded", "retracted"] = (
        "confirmed"
    )
    project_id: str | None = Field(None, max_length=200)
    action_status: Literal["open", "in_progress", "blocked", "done", "cancelled"] | None = None
    assignee: str | None = Field(None, max_length=500)
    due_at: AwareDatetime | None = None
    subject: str | None = Field(None, max_length=500)
    predicate: str | None = Field(None, max_length=500)
    object_value: str | None = Field(None, max_length=10_000)
    evidence_message_ids: list[int] | None = Field(None, max_length=500)
    evidence_excerpt: str | None = Field(None, max_length=12_000)
    evidence_refs: list[dict[str, object]] | None = Field(None, max_length=100)
    conflicts_with: list[str] | None = Field(None, max_length=100)
    meeting_ids: list[int] | None = Field(None, max_length=100)
    file_ids: list[int] | None = Field(None, max_length=100)
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None
    expires_in_days: int | None = Field(None, ge=-1)
    expires_at: AwareDatetime | None = Field(
        None,
        description="Absolute expiry timestamp used when re-importing an export",
    )


class MemoryBatchImportRequest(BaseModel):
    memories: list[MemoryBatchItem] = Field(
        ..., min_length=1, max_length=100, description="1-100 memories"
    )


class MemoryBatchImportResponse(BaseModel):
    imported: int
    failed: int
    errors: list[str] = Field(default_factory=list)


class MemoryBatchDeleteRequest(BaseModel):
    keys: list[str] = Field(..., min_length=1, max_length=100)

    @field_validator("keys")
    @classmethod
    def _validate_keys(cls, keys: list[str]) -> list[str]:
        cleaned = [key.strip() for key in keys]
        if any(not key or len(key) > 200 for key in cleaned):
            raise ValueError("Each memory key must contain 1-200 characters")
        return list(dict.fromkeys(cleaned))


class EntityBatchDeleteRequest(BaseModel):
    names: list[str] = Field(..., min_length=1, max_length=100)

    @field_validator("names")
    @classmethod
    def _validate_names(cls, names: list[str]) -> list[str]:
        cleaned = [name.strip().lower() for name in names]
        if any(not name or len(name) > 200 for name in cleaned):
            raise ValueError("Each entity name must contain 1-200 characters")
        return list(dict.fromkeys(cleaned))


class BatchDeleteResponse(BaseModel):
    deleted: int
    missing: list[str] = Field(default_factory=list)


class MemoryExportResponse(BaseModel):
    """Response for GET /memory/export"""

    user_id: str
    total: int
    memories: list[dict]
    next_cursor: str | None = None


class MemorySearchResultItem(MemoryResponse):
    """Full memory record plus the scores produced by semantic retrieval."""

    combined_score: float
    decay_score: float


class MemorySearchResponse(BaseModel):
    """Response for POST /memory/search"""

    memories: list[MemorySearchResultItem]
    total: int


class MemoryDecayResponse(BaseModel):
    """Response for POST /memory/decay"""

    decayed_count: int


class EntityResponse(BaseModel):
    """Knowledge-graph entity item"""

    id: int
    user_id: str
    name: str
    entity_type: str
    description: str | None = None
    mention_count: int = 0
    created_at: str
    updated_at: str
    aliases: list[str] = Field(default_factory=list)


class EntityRelationResponse(BaseModel):
    """A single relation connected to an entity"""

    predicate: str
    other_id: int
    other_name: str
    other_type: str
    direction: str
    confidence: float = 1.0
    evidence_message_ids: list[int] | None = None
    valid_from: str | None = None
    valid_to: str | None = None

    @field_validator("evidence_message_ids", mode="before")
    @classmethod
    def _decode_evidence_ids(cls, value):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return None
        return value if isinstance(value, list) else None


class EntityWithRelationsResponse(BaseModel):
    """Entity with all its relations"""

    entity: EntityResponse
    relations: list[EntityRelationResponse]


class EntityListResponse(BaseModel):
    """Response for GET /memory/entities"""

    entities: list[EntityResponse]
    total: int
    next_cursor: str | None = None
