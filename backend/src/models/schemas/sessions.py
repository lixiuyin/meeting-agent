"""Session-related Pydantic models."""

from pydantic import BaseModel, Field

from ._common import UTCDatetime


class SessionResponse(BaseModel):
    id: str
    user_id: str
    title: str | None = None
    created_at: UTCDatetime
    updated_at: UTCDatetime


class SessionListResponse(BaseModel):
    items: list[SessionResponse]
    next_cursor: str | None = None
    total: int | None = None
    # Backward compatibility for existing frontend clients.
    sessions: list[SessionResponse] | None = None


class SessionSourceResponse(BaseModel):
    """Source provenance for a historical chat message."""

    meeting_id: int | None = None
    meeting_title: str = ""
    content: str = ""
    score: float = 0.0
    file_id: int | None = None
    file_name: str | None = None
    file_type: str | None = None
    chunk_index: int | None = None
    page_number: int | None = None
    timestamp_start: float | None = None
    timestamp_end: float | None = None
    speaker: str | None = None


class SessionMessageResponse(BaseModel):
    role: str
    content: str
    sources: list[SessionSourceResponse] = Field(default_factory=list)


class SessionDetailResponse(BaseModel):
    session: SessionResponse
    messages: list[SessionMessageResponse]
    total: int


class SessionSummaryResponse(BaseModel):
    session_id: str
    summary: str
    topics: list[str] = Field(default_factory=list)
    key_entities: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    turn_count: int = 0
    session_title: str | None = None
    created_at: str | None = None


class SessionSummaryListResponse(BaseModel):
    """Response for GET /sessions/summaries"""

    items: list[SessionSummaryResponse]
    next_cursor: str | None = None
    total: int | None = None
    # Backward compatibility for existing frontend clients.
    summaries: list[SessionSummaryResponse] | None = None


class SessionSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Search query for past conversations")
    limit: int = Field(10, ge=1, le=50)


class SessionSearchResult(BaseModel):
    type: str  # "session_summary" or "message"
    session_id: str
    summary: str | None = None
    topics: list[str] = Field(default_factory=list)
    session_title: str | None = None
    role: str | None = None
    content: str | None = None
    created_at: str | None = None


class SessionSearchResponse(BaseModel):
    results: list[SessionSearchResult]
    total: int
