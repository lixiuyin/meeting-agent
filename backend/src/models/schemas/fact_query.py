"""Structured results are data, not a model's claim of list completeness."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, PositiveInt, model_validator

from .memory import MemoryResponse


class FactQueryRequest(BaseModel):
    query: str = Field("", max_length=2000)
    fact_types: list[Literal["fact", "preference", "project_fact", "decision", "action_item"]] = (
        Field(
            default_factory=lambda: ["decision", "action_item", "project_fact"],
            min_length=1,
            max_length=5,
        )
    )
    action_status: list[Literal["open", "in_progress", "blocked", "done", "cancelled"]] = Field(
        default_factory=list, max_length=5
    )
    assignee: str | None = Field(None, max_length=500)
    overdue: bool = False
    project_id: str | None = Field(None, max_length=200)
    meeting_ids: list[PositiveInt] = Field(default_factory=list, max_length=100)
    file_ids: list[PositiveInt] = Field(default_factory=list, max_length=100)
    valid_at: AwareDatetime | None = None
    known_at: AwareDatetime | None = None
    limit: int = Field(50, ge=1, le=100)
    offset: int = Field(0, ge=0)
    snapshot: str | None = Field(None, max_length=64)


class FactQueryResponse(BaseModel):
    items: list[MemoryResponse]
    total: int
    returned: int
    next_offset: int | None
    snapshot: str
    recorded_set_complete: bool
    extraction_complete: bool = False
    coverage_note: str = (
        "Recorded facts only; this does not certify complete extraction of the source materials."
    )
    scope: dict


class FactChangesRequest(FactQueryRequest):
    before: AwareDatetime
    after: AwareDatetime

    @model_validator(mode="after")
    def ordered(self):
        if self.before >= self.after:
            raise ValueError("before must precede after")
        if self.valid_at is not None:
            raise ValueError("Use before/after instead of valid_at when comparing states")
        return self


class FactChange(BaseModel):
    key: str
    kind: Literal["added", "removed", "changed"]
    changed_fields: list[str]
    before: MemoryResponse | None = None
    after: MemoryResponse | None = None


class FactChangesResponse(BaseModel):
    items: list[FactChange]
    total: int
    next_offset: int | None
    snapshot: str
    extraction_complete: bool = False
    coverage_note: str = (
        "Changes in recorded facts, not proof that every source change was extracted."
    )
