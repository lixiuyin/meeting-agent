"""Stable UI contract for read-only session continuation preflight."""

from typing import Literal

from pydantic import BaseModel, Field


class ContinuationFile(BaseModel):
    file_id: int
    file_name: str | None = None
    status: Literal["unchanged", "changed", "deleted", "rejected", "unverified"]


class ContinuationMemoryChange(BaseModel):
    key: str
    saved_revision: int | None = None
    current_revision: int | None = None
    status: Literal["deleted", "changed", "inactive"]


class ContinuationPreviewResponse(BaseModel):
    session_id: str
    scope: dict = Field(default_factory=dict)
    files: list[ContinuationFile]
    memory_changes: list[ContinuationMemoryChange]
    open_questions: list[str]
    saved_snapshot_available: bool
    checkpoint_available: bool
    messages_since_checkpoint: int
    notice: str
