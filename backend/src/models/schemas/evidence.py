"""Versioned evidence-location contract shared by every source viewer."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class EvidenceLocationRequest(BaseModel):
    block_id: str | None = Field(default=None, max_length=100)
    source_revision: str | None = Field(default=None, max_length=200)
    window_start: int | None = Field(default=None, ge=0)
    window_end: int | None = Field(default=None, ge=0)
    excerpt: str | None = Field(default=None, max_length=10000)
    page: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def valid_window(self):
        if (self.window_start is None) != (self.window_end is None):
            raise ValueError("Both evidence window bounds are required")
        if (
            self.window_start is not None
            and self.window_end is not None
            and self.window_end <= self.window_start
        ):
            raise ValueError("Evidence window must be nonempty")
        return self


class EvidenceLocationResponse(BaseModel):
    block_id: str | None = None
    status: Literal["exact", "page_only", "ambiguous", "not_found", "version_changed"]
    meeting_id: int
    file_id: int
    source_revision: str
    parser_revision: str
    evidence_id: str | None = None
    page: int | None = None
    window_start: int | None = None
    window_end: int | None = None
    excerpt: str | None = None
    timestamp_start: float | None = None
    timestamp_end: float | None = None
    reason: str | None = None
