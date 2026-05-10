"""Vision service public API."""

from ._batch import caption_images_batch
from ._captioner import (
    CombinedImageContent,
    ImageCaption,
    caption_image,
    describe_image_semantics,
    extract_image_content,
    is_meaningful_caption,
    is_meaningful_ocr_text,
    transcribe_text_bearing_image,
)
from ._client import close_vision_client, get_vision_client
from ._dedupe import deduplicate_caption_ocr

__all__ = [
    "CombinedImageContent",
    "ImageCaption",
    "caption_image",
    "caption_images_batch",
    "close_vision_client",
    "deduplicate_caption_ocr",
    "describe_image_semantics",
    "extract_image_content",
    "get_vision_client",
    "is_meaningful_caption",
    "is_meaningful_ocr_text",
    "transcribe_text_bearing_image",
]
