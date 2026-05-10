"""Shared types for pluggable file processors."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from ....core.trace import TraceContext
from ...parser.types import ParsedDocument

StructuredKind = Literal["segments", "pages", "captions"] | None


@dataclass(frozen=True)
class ProcessorContext:
    file_id: int
    meeting_id: int
    file_type: str
    file_name: str
    file_path: Path
    meeting_date: str | None
    trace: TraceContext | None


@dataclass(frozen=True)
class FileArtefact:
    text: str
    structured_json: str | None
    structured_kind: StructuredKind
    metrics: dict[str, int | float | str]
    segments: list[dict] | None = None
    aux_segments: list[dict] | None = None
    parsed_doc: ParsedDocument | None = None


class FileProcessor(Protocol):
    kind: str

    async def process(self, ctx: ProcessorContext) -> FileArtefact: ...
