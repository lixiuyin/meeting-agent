"""Session-related Pydantic models."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ._common import UTCDatetime
from .chat import SourceResponse


class SessionResponse(BaseModel):
    id: str
    user_id: str
    title: str | None = None
    created_at: UTCDatetime
    updated_at: UTCDatetime
    parent_session_id: str | None = None
    branched_from_message_id: int | None = None
    branch_reason: str | None = None


class SessionListResponse(BaseModel):
    items: list[SessionResponse]
    next_cursor: str | None = None
    total: int | None = None
    # Backward compatibility for existing frontend clients.
    sessions: list[SessionResponse] | None = None


class SessionBatchDeleteRequest(BaseModel):
    session_ids: list[str] = Field(..., min_length=1, max_length=100)
    retract_derived_memories: bool = False

    @field_validator("session_ids")
    @classmethod
    def _deduplicate(cls, session_ids: list[str]) -> list[str]:
        cleaned = [session_id.strip() for session_id in session_ids]
        if any(not session_id or len(session_id) > 64 for session_id in cleaned):
            raise ValueError("Each session ID must contain 1-64 characters")
        return list(dict.fromkeys(cleaned))


class SessionBatchDeleteResponse(BaseModel):
    deleted: int
    missing: list[str] = Field(default_factory=list)


class SessionSourceResponse(SourceResponse):
    """The same provenance contract as live chat, with legacy-safe defaults."""

    meeting_id: int | None = None
    document_revision: str | None = None
    memory_key: str | None = None
    window_start: int | None = None
    window_end: int | None = None
    evidence_excerpt: str | None = None
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
    degraded: bool = False
    degradation_reason: str | None = None
    id: int | None = None
    role: str
    content: str
    sources: list[SessionSourceResponse] = Field(default_factory=list)


class SessionDetailResponse(BaseModel):
    session: SessionResponse
    messages: list[SessionMessageResponse]
    total: int
    next_before_id: int | None = None
    pending_run: dict | None = None
    session_config: dict | None = None
    task_state: dict | None = None


class SessionBranchRequest(BaseModel):
    from_message_id: int = Field(..., ge=1)
    reason: Literal["edit", "regenerate"]


class SessionBranchResponse(BaseModel):
    session: SessionResponse
    messages: list[SessionMessageResponse]
    total: int
    next_before_id: int | None = None


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
    query: str = Field(
        ..., min_length=1, max_length=2_000, description="Search query for past conversations"
    )
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
