"""Batch helpers for image captioning."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from ._captioner import ImageCaption, caption_image

logger = logging.getLogger(__name__)


async def caption_images_batch(
    image_paths: list[str | Path], *, max_concurrency: int = 4
) -> list[ImageCaption | None]:
    """Caption multiple images concurrently with bounded fan-out."""
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _run(image_path: str | Path) -> ImageCaption | None:
        async with semaphore:
            caption = await caption_image(image_path)
            if caption is None:
                return None
            return ImageCaption(caption=caption)

    # L-2: return_exceptions=True so one image failure doesn't cancel the
    # entire batch.  Filter out exceptions post-hoc and log them.
    results: list[Any] = await asyncio.gather(
        *(_run(path) for path in image_paths), return_exceptions=True
    )
    output: list[ImageCaption | None] = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.warning("Caption failed for image %s: %s", image_paths[i], result)
            output.append(None)
        else:
            output.append(result)
    return output
