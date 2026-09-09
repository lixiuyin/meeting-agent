"""Stable identity for settings that determine native index contents."""

from __future__ import annotations

import hashlib
import json
from typing import Any

INDEX_CONFIG_FIELDS = (
    "EMBEDDING_BINDING",
    "EMBEDDING_MODEL",
    "EMBEDDING_DIMENSION",
    "EMBEDDING_BASE_URL",
    "EMBEDDING_HOST",
    "DISTANCE_METRIC",
    "CHUNK_SIZE",
    "CHUNK_SIZE_TOKENS",
    "CHUNK_OVERLAP",
    "PARENT_CHILD_ENABLED",
    "CHILD_CHUNK_SIZE",
    "CHILD_CHUNK_SIZE_TOKENS",
    "CHUNK_OVERLAP_TOKENS",
    "CHILD_CHUNK_OVERLAP_TOKENS",
    "CHILD_CHUNK_OVERLAP",
    "SEMANTIC_CHUNKING_ENABLED",
    "NON_TEXT_CHUNKING_STRATEGY",
    "RAG_INDEX_TABLES",
    "RAG_INDEX_IMAGE_CAPTIONS",
    "RAG_IMAGE_OCR_MIN_LENGTH",
    "AUDIO_SPEAKER_IN_CONTENT",
    "AUDIO_SPLIT_ON_SPEAKER_CHANGE",
    "AUDIO_SEMANTIC_BOUNDARY_ENABLED",
    "AUDIO_SEMANTIC_BOUNDARY_THRESHOLD",
    "AUDIO_SEMANTIC_MIN_SEGMENTS",
    "AUDIO_SEMANTIC_MAX_SEGMENTS",
)
CONTEXTUAL_RETRIEVAL_VERSION = 4


def index_config_fingerprint(source: Any | None = None) -> str:
    """Hash only settings that change embeddings, chunk identity, or contents."""
    if source is None:
        from .config import settings

        source = settings
    payload = {name: getattr(source, name) for name in INDEX_CONFIG_FIELDS}
    payload["CONTEXTUAL_RETRIEVAL_VERSION"] = CONTEXTUAL_RETRIEVAL_VERSION
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()[:24]


def index_config_changed(before: Any, after: Any) -> list[str]:
    """Return index-shaping fields whose values differ."""
    return [name for name in INDEX_CONFIG_FIELDS if getattr(before, name) != getattr(after, name)]
