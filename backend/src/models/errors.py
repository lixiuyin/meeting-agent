"""Unified error code protocol for structured API error responses.

Provides a canonical set of machine-readable error codes (``ErrorCode``) and
a lightweight Pydantic model (``ApiErrorResponse``) that routers can use to
return consistent, typed error payloads.

Other routers will be migrated incrementally; this module is the single source
of truth for the error code vocabulary.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class ErrorCode(StrEnum):
    """Machine-readable error codes returned by all API endpoints."""

    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    CONFLICT = "CONFLICT"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    LLM_ERROR = "LLM_ERROR"
    STREAM_ERROR = "STREAM_ERROR"
    STREAM_QUEUE_FULL = "STREAM_QUEUE_FULL"
    STREAM_BACKPRESSURE_LIMIT = "STREAM_BACKPRESSURE_LIMIT"
    STREAM_UNEXPECTED_END = "STREAM_UNEXPECTED_END"
    SESSION_BUSY = "SESSION_BUSY"


class ApiErrorResponse(BaseModel):
    """Structured error payload for API responses.

    Usage::

        raise HTTPException(
            status_code=500,
            detail=ApiErrorResponse(
                error="Failed to process chat request",
                code=ErrorCode.INTERNAL_ERROR,
            ).model_dump(),
        )
    """

    error: str = Field(description="Human-readable error message")
    code: ErrorCode = Field(description="Machine-readable error code")
    detail: str | None = Field(
        None,
        description="Optional additional context for debugging",
    )
