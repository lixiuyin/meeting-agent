"""Shared fixtures for micro-benchmark tests."""

import random
import string

import pytest


@pytest.fixture(scope="module")
def seeded_vectorstore():
    """Seed the Chroma vectorstore with ~1k documents using random vectors.

    The embedding dimension is read from the collection (or sampled from an
    existing vector) so this fixture works regardless of which embedding
    binding the test environment is configured with.
    """
    from src.services.rag._vectorstore import get_vectorstore

    vectorstore = get_vectorstore()
    dim = _detect_collection_dimension(vectorstore)

    docs = []
    ids = []
    embeddings = []
    for i in range(1000):
        text = "".join(random.choices(string.ascii_lowercase + " ", k=80))
        docs.append(text)
        ids.append(f"doc_{i}")
        embeddings.append([random.random() for _ in range(dim)])

    vectorstore._collection.upsert(
        ids=ids,
        documents=docs,
        embeddings=embeddings,
        metadatas=[{"source": "benchmark"} for _ in ids],
    )
    yield vectorstore
    # Cleanup
    vectorstore._collection.delete(where={"source": "benchmark"})


def _detect_collection_dimension(vectorstore) -> int:
    """Return the dimension the Chroma collection expects.

    Checks (in order): collection metadata → sampled existing vector →
    settings.EMBEDDING_DIMENSION → conservative 384 fallback for mock setups.
    """
    collection = vectorstore._collection

    metadata_dim = (collection.metadata or {}).get("embedding_dimension")
    if isinstance(metadata_dim, int) and metadata_dim > 0:
        return metadata_dim

    try:
        peek = collection.get(include=["embeddings"], limit=1)
        embs = peek.get("embeddings")
        if embs is not None and len(embs) > 0:
            first = embs[0]
            if first is not None and hasattr(first, "__len__"):
                return len(first)
    except Exception:
        pass

    try:
        from src.core.config import settings

        cfg_dim = getattr(settings, "EMBEDDING_DIMENSION", None)
        if isinstance(cfg_dim, int) and cfg_dim > 0:
            return cfg_dim
    except Exception:
        pass

    return 384
