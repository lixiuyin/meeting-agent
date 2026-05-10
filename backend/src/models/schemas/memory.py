"""Memory-related Pydantic models."""

from pydantic import BaseModel, Field


class MemorySetRequest(BaseModel):
    key: str = Field(..., min_length=1, max_length=200)
    value: str = Field(..., min_length=1, max_length=10000)
    importance: float = Field(3.0, ge=1.0, le=5.0, description="Importance score 1-5")
    category: str | None = Field(None, max_length=100, description="Memory category tag")
    expires_in_days: int | None = Field(
        None, ge=-1, description="TTL in days (-1=never, default 90)"
    )


class MemoryResponse(BaseModel):
    key: str
    value: str
    source: str
    importance: float = 3.0
    category: str | None = None
    access_count: int = 0
    expires_at: str | None = None
    last_accessed: str | None = None
    updated_at: str
    relevance_score: float | None = None
    superseded_by: str | None = None
    session_id: str | None = None


class MemoryUpdateRequest(BaseModel):
    key: str = Field(..., min_length=1, max_length=200)
    value: str | None = Field(None, min_length=1, max_length=10000)
    importance: float | None = Field(None, ge=1.0, le=5.0, description="Importance score 1-5")
    category: str | None = Field(None, max_length=100, description="Memory category tag")


class MemoryListResponse(BaseModel):
    items: list[MemoryResponse]
    next_cursor: str | None = None
    total: int | None = None
    # Backward compatibility for existing frontend clients.
    memories: list[MemoryResponse] | None = None


class MemorySearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Semantic search query for memories")
    limit: int = Field(5, ge=1, le=20)
    min_importance: float = Field(1.0, ge=1.0, le=5.0)


class MemoryBatchItem(BaseModel):
    key: str = Field(..., min_length=1, max_length=200)
    value: str = Field(..., min_length=1, max_length=10000)
    importance: float = Field(3.0, ge=1.0, le=5.0)
    category: str | None = Field(None, max_length=100)
    expires_in_days: int | None = Field(None, ge=-1)


class MemoryBatchImportRequest(BaseModel):
    memories: list[MemoryBatchItem] = Field(
        ..., min_length=1, max_length=100, description="1-100 memories"
    )


class MemoryBatchImportResponse(BaseModel):
    imported: int
    failed: int
    errors: list[str] = Field(default_factory=list)


class MemoryExportResponse(BaseModel):
    """Response for GET /memory/export"""

    user_id: str
    total: int
    memories: list[dict]


class MemorySearchResultItem(BaseModel):
    """Single result from semantic memory search"""

    key: str
    value: str
    importance: float
    category: str | None = None
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
    aliases: list[str] = []


class EntityRelationResponse(BaseModel):
    """A single relation connected to an entity"""

    predicate: str
    other_id: int
    other_name: str
    other_type: str
    direction: str


class EntityWithRelationsResponse(BaseModel):
    """Entity with all its relations"""

    entity: EntityResponse
    relations: list[EntityRelationResponse]


class EntityListResponse(BaseModel):
    """Response for GET /memory/entities"""

    entities: list[EntityResponse]
    total: int
