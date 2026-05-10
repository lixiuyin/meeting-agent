"""Image processor (OCR text + captions container)."""

import asyncio
import json

from ....core.config import settings
from ...parser import parse_structured
from ...vision import (
    caption_image,
    deduplicate_caption_ocr,
    is_meaningful_caption,
    is_meaningful_ocr_text,
)
from ._types import FileArtefact, ProcessorContext


class ImageFileProcessor:
    kind = "image"

    async def process(self, ctx: ProcessorContext) -> FileArtefact:
        parsed_doc = await asyncio.wait_for(
            asyncio.to_thread(parse_structured, ctx.file_path, trace=ctx.trace),
            timeout=settings.PARSE_TIMEOUT_SECONDS,
        )
        extracted_text = parsed_doc.to_text()
        ocr_text = extracted_text.strip() if extracted_text and extracted_text.strip() else None
        ocr_text = ocr_text if is_meaningful_ocr_text(ocr_text) else None

        vision_caption = await caption_image(str(ctx.file_path))
        caption = vision_caption if is_meaningful_caption(vision_caption) else None
        caption, ocr_text = await deduplicate_caption_ocr(caption, ocr_text)

        indexed_parts = [part for part in (caption, ocr_text) if part]
        text = "\n\n".join(indexed_parts)
        segments = (
            [{"start": 0.0, "end": 0.0, "text": text, "speaker": "image"}]
            if indexed_parts
            else None
        )
        captions = [{"caption": caption, "ocr_text": ocr_text}]
        metrics: dict[str, int | float | str] = {
            "word_count": len(text.split()),
            "image_caption_success_count": 1 if caption else 0,
            "image_ocr_success_count": 1 if ocr_text else 0,
        }
        return FileArtefact(
            text=text,
            structured_json=json.dumps(captions),
            structured_kind="captions",
            metrics=metrics,
            segments=segments,
            aux_segments=None,
            parsed_doc=parsed_doc,
        )
