"""Chat-related Pydantic models."""

import datetime
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, Field

from ...core.operating_modes import MemoryMode, RetrievalProfile


class SourceResponse(BaseModel):
    """A referenced source chunk from meeting content"""

    meeting_id: int
    meeting_title: str
    document_revision: str | None = None
    chunk_id: str | None = None
    source_id: str | None = Field(
        None, description="Original chunk ID or a separately namespaced derived-summary identity"
    )
    memory_key: str | None = None
    window_start: int | None = None
    window_end: int | None = None
    evidence_excerpt: str | None = None
    alternate_sources: list[dict[str, Any]] = Field(default_factory=list)
    content: str = Field(description="Preview of the matching content")
    score: float = Field(description="Similarity score")
    file_id: int | None = Field(None, description="File ID within the meeting")
    file_name: str | None = Field(None, description="Original file name")
    chunk_index: int | None = Field(None, description="Chunk position in the file")
    page_number: int | None = Field(None, description="Page/slide number (documents)")
    slide_number: int | None = Field(None, description="Slide number (presentation files)")
    timestamp_start: float | None = Field(None, description="Start time in seconds (audio/video)")
    timestamp_end: float | None = Field(None, description="End time in seconds (audio/video)")
    speaker: str | None = Field(None, description="Speaker label (if diarized)")
    file_type: str | None = Field(
        None, description="File type (video, audio, pdf, ppt, image, etc.)"
    )
    source_kind: str | None = Field(
        None,
        description="Source kind (timestamp, slide, page, image, text, "
        "meeting_summary, file_summary)",
    )
    content_type: str | None = Field(
        None, description="Chunk type (text, table, image_caption, image_ocr, image_combined)"
    )
    image_caption: str | None = Field(None, description="Image caption snippet when available")
    image_ocr: str | None = Field(None, description="Image OCR text snippet when available")
    table_markdown: str | None = Field(None, description="Table markdown snippet when available")
    image_path: str | None = Field(None, description="Relative storage path for cited image asset")
    image_thumbnail_path: str | None = Field(
        None, description="Relative storage path for cited image thumbnail"
    )
    page_image_path: str | None = Field(
        None, description="Relative storage path for cited source page image"
    )
    page_image_thumbnail_path: str | None = Field(
        None, description="Relative storage path for cited source page thumbnail"
    )
    heading_path: list[str] = Field(
        default_factory=list, description="Document heading path for this chunk"
    )
    confidence: float | None = Field(None, description="Parser confidence score when available")


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=10_000)
    meeting_ids: list[int] | None = Field(
        None,
        max_length=50,
        description="Restrict search to these meeting IDs; empty means all",
    )
    file_ids: list[int] | None = Field(
        None,
        max_length=200,
        description="Restrict search to these file IDs; empty means all files",
    )
    session_id: str | None = Field(
        None, description="Session ID for conversation continuity; creates new session if omitted"
    )
    top_k: int | None = Field(None, ge=1, le=50)
    use_web_search: bool = Field(
        False,
        description=(
            "Backward-compatible web augmentation switch. true maps to web_search_mode='always'."
        ),
    )
    web_search_mode: Literal["off", "fallback", "always"] | None = Field(
        None,
        description=(
            "Web-search policy. 'always' augments every retrieval answer; 'fallback' searches "
            "unless a calibrated local-confidence signal is available and sufficient; 'off' "
            "disables web search. Overrides use_web_search when provided."
        ),
    )
    web_search_results: int | None = Field(
        None, ge=1, le=20, description="Number of web search results"
    )
    file_types: list[str] | None = Field(
        None,
        max_length=20,
        description="Filter by file types (e.g. ['pdf', 'video'])",
    )
    date_from: datetime.date | None = Field(
        None, description="ISO date — only meetings on or after this date"
    )
    date_to: datetime.date | None = Field(
        None, description="ISO date — only meetings on or before this date"
    )
    valid_at: AwareDatetime | None = Field(
        None,
        description="Business-time snapshot used for long-term memory facts",
    )
    known_at: AwareDatetime | None = Field(
        None,
        description="System-time cutoff: only facts recorded by this instant are visible",
    )
    rag_mode: (
        Literal["vector", "native", "hybrid", "multimodal", "hybrid_multimodal", "auto"] | None
    ) = Field(
        None,
        description="Per-request retrieval mode override",
    )
    retrieval_profile: RetrievalProfile = Field(
        "balanced",
        description="Retrieval cost/quality preset; explicit top_k still wins",
    )
    memory_mode: MemoryMode = Field(
        "balanced",
        description="Long-term memory operating mode",
    )
    continuation_mode: Literal["latest", "saved_scope", "saved_snapshot"] = Field(
        "latest",
        description=(
            "Resume with current state, restore only the prior scope, or replay "
            "the prior turn's saved evidence"
        ),
    )


class WebResultResponse(BaseModel):
    """Web search result item"""

    title: str
    url: str
    snippet: str


class PastSessionRef(BaseModel):
    """Reference to a past session that informed the answer"""

    session_id: str
    title: str = ""
    created_at: str = ""
    summary_preview: str = ""
    score: float = 0.0


class ChatResponse(BaseModel):
    answer: str
    degraded: bool = Field(
        False,
        description="True when the answer used a bounded degraded-generation fallback",
    )
    degradation_reason: str | None = Field(
        None,
        description="Machine-readable reason for a degraded answer",
    )
    sources: list[SourceResponse] = Field(
        default_factory=list, description="Referenced source chunks"
    )
    session_id: str = Field(description="Session ID for continuing the conversation")
    web_results: list[WebResultResponse] | None = Field(
        None, description="Web search results if web search was enabled"
    )
    past_sessions: list[PastSessionRef] = Field(
        default_factory=list, description="Past sessions that informed this answer"
    )
    extraction_failed: bool = Field(
        False,
        description="True if background fact extraction failed after all retries",
    )
    trace: dict | None = Field(None, description="Pipeline execution trace with timing per step")
    context_truncated: int | None = Field(
        None,
        description="Number of chunks dropped due to token budget (null if no truncation)",
    )


class SearchChunkItem(BaseModel):
    """Single search result from retrieval-only endpoint"""

    meeting_id: int
    meeting_title: str
    content: str = Field(description="Preview of the matching content")
    score: float = Field(description="Similarity score")


class SearchChunksResponse(BaseModel):
    """Response for /chat/search endpoint"""

    results: list[SearchChunkItem]
    total: int = Field(description="Total number of results")


# ---------------------------------------------------------------------------
# SSE Stream Event models
# ---------------------------------------------------------------------------


class StepEvent(BaseModel):
    type: Literal["step"] = "step"
    event_version: Literal["1"] = "1"
    step: str
    status: str
    duration_ms: float | None = None


class TokenEvent(BaseModel):
    type: Literal["token"] = "token"
    event_version: Literal["1"] = "1"
    content: str


class SourcesEvent(BaseModel):
    type: Literal["sources"] = "sources"
    event_version: Literal["1"] = "1"
    sources: list[SourceResponse]


class TraceEvent(BaseModel):
    type: Literal["trace"] = "trace"
    event_version: Literal["1"] = "1"
    trace: dict[str, Any]


class WebResultsEvent(BaseModel):
    type: Literal["web_results"] = "web_results"
    event_version: Literal["1"] = "1"
    results: list[WebResultResponse]


class StatusEvent(BaseModel):
    """Non-answer status emitted when generation enters a degraded mode."""

    type: Literal["status"] = "status"
    event_version: Literal["1"] = "1"
    status: str
    reason: str | None = None


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    event_version: Literal["1"] = "1"
    message: str
    code: str | None = None
    detail: str | None = None
    exception_type: str | None = None


# Canonical SSE error codes for structured error handling
SSE_ERROR_RETRIABLE_TIMEOUT = "RETRIABLE_TIMEOUT"
SSE_ERROR_LLM_RATELIMIT = "LLM_RATELIMIT"
SSE_ERROR_INTERNAL = "INTERNAL"


class DoneEvent(BaseModel):
    type: Literal["done"] = "done"
    event_version: Literal["1"] = "1"
    session_id: str


class HeartbeatEvent(BaseModel):
    type: Literal["heartbeat"] = "heartbeat"
    event_version: Literal["1"] = "1"


StreamEvent = (
    StepEvent
    | TokenEvent
    | SourcesEvent
    | TraceEvent
    | WebResultsEvent
    | StatusEvent
    | ErrorEvent
    | DoneEvent
    | HeartbeatEvent
)
