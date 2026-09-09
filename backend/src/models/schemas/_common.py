"""Common enums and shared models."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Generic, TypeVar

from pydantic import AfterValidator, BaseModel, Field


def _ensure_utc(value: datetime) -> datetime:
    """Attach UTC tzinfo to naive datetimes so they serialize with the
    timezone designator required by the OpenAPI ``date-time`` format.

    SQLite stores ``CURRENT_TIMESTAMP`` as a naive ``YYYY-MM-DD HH:MM:SS``
    string; pydantic parses it into a naive ``datetime`` which then
    serializes as ``"2026-05-09T14:28:17"`` — missing the timezone that
    RFC 3339 / OpenAPI's ``date-time`` format requires. The application
    convention (per CLAUDE.md) treats all stored datetimes as UTC, so
    attaching ``UTC`` is the correct lossless interpretation.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


UTCDatetime = Annotated[datetime, AfterValidator(_ensure_utc)]


class FileType(StrEnum):
    VIDEO = "video"
    AUDIO = "audio"
    PDF = "pdf"
    PPT = "ppt"
    DOC = "doc"
    XLS = "xls"
    CSV = "csv"
    TXT = "txt"
    IMAGE = "image"


class MeetingStatus(StrEnum):
    UPLOADING = "uploading"
    PROCESSING = "processing"
    SUMMARIZING = "summarizing"
    READY = "ready"
    FAILED = "failed"
    ERROR = "error"


class ExportFormat(StrEnum):
    JSON = "json"
    MARKDOWN = "markdown"
    TXT = "txt"


class MessageResponse(BaseModel):
    """Generic message response for DELETE and action endpoints."""

    message: str


class ErrorResponse(BaseModel):
    """Unified error envelope for all API responses."""

    code: str = Field(description="Machine-readable error code")
    message: str = Field(description="Human-readable error message")
    request_id: str = Field(description="Request ID for tracing")
    details: dict[str, Any] | None = Field(None, description="Additional error context")


class RequestValidationErrorResponse(BaseModel):
    """FastAPI request-validation errors retain the standard detail array."""

    detail: list[dict[str, Any]]


class PaginationParams(BaseModel):
    """Standard pagination parameters for list endpoints.

    cursor is a base64-encoded offset integer.
    """

    limit: int = Field(50, ge=1, le=100, description="Maximum items to return")
    cursor: str | None = Field(None, description="Opaque pagination cursor")


T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    """Standard paginated list response envelope."""

    items: list[T]
    next_cursor: str | None = Field(None, description="Cursor for the next page")
    total: int | None = Field(None, description="Total number of items available")
