"""Batch embedding adapter to avoid API rate/size limits."""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def embed_documents_batched(
    embeddings: Any,
    texts: list[str],
    batch_size: int = 64,
) -> list[list[float]]:
    """Embed documents in batches to avoid API rate/size limits.

    Splits the input texts into chunks of ``batch_size``, calls
    ``embeddings.embed_documents()`` on each chunk, and concatenates
    the results.

    Args:
        embeddings: LangChain Embeddings instance.
        texts: List of text strings to embed.
        batch_size: Maximum number of texts per API call.

    Returns:
        Flat list of embedding vectors, one per input text.
    """
    if not texts:
        return []

    if len(texts) <= batch_size:
        return embeddings.embed_documents(texts)

    results: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        results.extend(embeddings.embed_documents(batch))
        logger.debug("Embedded batch %d-%d/%d", i, i + len(batch), len(texts))

    return results
