"""Plain-text passthrough processor."""

import asyncio

from ...parser import parse
from ._types import FileArtefact, ProcessorContext


class TextFileProcessor:
    kind = "text"

    async def process(self, ctx: ProcessorContext) -> FileArtefact:
        text = await asyncio.to_thread(parse, ctx.file_path)
        metrics: dict[str, int | float | str] = {"word_count": len(text.split())}
        return FileArtefact(
            text=text,
            structured_json=None,
            structured_kind=None,
            metrics=metrics,
            segments=None,
            aux_segments=None,
            parsed_doc=None,
        )
