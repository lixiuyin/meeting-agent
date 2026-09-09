"""Per-file fair retrieval: guarantees each in-scope file contributes chunks."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ...core.config import settings
from ...core.metrics import FAIR_RETRIEVE_CACHE_HITS
from ._retriever import retrieve

logger = logging.getLogger(__name__)


def _resolve_chunks_per_file(
    file_id: int,
    chunks_per_file: int | dict[int, int],
    default: int,
) -> int:
    """Resolve per-file chunk budget from int or dict specification."""
    if isinstance(chunks_per_file, int):
        return chunks_per_file or default
    return chunks_per_file.get(file_id, default) or default


async def fair_retrieve_per_file(
    query: str,
    scope_file_ids: list[int],
    *,
    chunks_per_file: int | dict[int, int] = 2,
    trace: Any | None = None,
    rag_mode: str | None = None,
    known_speakers: list[str] | None = None,
    cached_docs: dict[int, list[dict]] | None = None,
    user_id: str | None = None,
    analysis_query: str | None = None,
    lexical_query: str | None = None,
) -> list[dict]:
    """Retrieve chunks across every file in *scope_file_ids*.

    For each file, runs ``retrieve()`` scoped to that single file with
    ``top_k=chunks_per_file``.  Results are merged and deduplicated.

    *chunks_per_file* can be a uniform ``int`` applied to every file, or a
    ``dict[int, int]`` mapping individual file IDs to their chunk budget.

    *cached_docs* maps file_id to docs from a prior wide fetch.  When a file
    has enough cached docs to satisfy its budget, the Chroma call is skipped.

    Concurrency is bounded by ``settings.RAG_FAIR_CONCURRENCY`` via a
    semaphore so the vectorstore is not overwhelmed.
    """
    if not scope_file_ids:
        return []

    default_chunks = settings.RAG_MIN_CHUNKS_PER_FILE
    concurrency = settings.RAG_FAIR_CONCURRENCY
    sem = asyncio.Semaphore(concurrency)

    async def _retrieve_one(file_id: int) -> list[dict]:
        budget = _resolve_chunks_per_file(file_id, chunks_per_file, default_chunks)
        per_file_fetch = max(budget * 2, budget + 2)

        # Consume cache when available and sufficient
        file_cache = (cached_docs or {}).get(file_id)
        if file_cache and len(file_cache) >= per_file_fetch:
            FAIR_RETRIEVE_CACHE_HITS.labels(result="hit").inc()
            return file_cache[:per_file_fetch]

        def _sync() -> list[dict]:
            docs, _ = retrieve(
                query,
                file_ids=[file_id],
                top_k=per_file_fetch,
                fetch_multiplier=1,
                trace=trace,
                rag_mode=rag_mode,
                known_speakers=known_speakers,
                user_id=user_id,
                analysis_query=analysis_query,
                lexical_query=lexical_query,
            )
            return docs

        async with sem:
            FAIR_RETRIEVE_CACHE_HITS.labels(result="miss").inc()
            return await asyncio.to_thread(_sync)

    tasks = [_retrieve_one(fid) for fid in scope_file_ids]
    per_file_results = await asyncio.gather(*tasks, return_exceptions=True)

    import hashlib as _hashlib

    seen: set[str] = set()
    merged: list[dict] = []
    missing_files: list[int] = []
    for fid, result in zip(scope_file_ids, per_file_results, strict=True):
        if isinstance(result, BaseException):
            logger.error(
                "Fair retrieval failed for file_id=%d: %s",
                fid,
                result,
            )
            missing_files.append(fid)
            continue
        for doc in result:
            meta = doc.get("metadata", {})
            # Use chunk_id first (stable across retries), then chunk_index,
            # then 32-hex content hash as last resort (H-RAG-3).
            chunk_id = meta.get("chunk_id")
            if chunk_id:
                key = str(chunk_id)
            else:
                chunk_idx = meta.get("chunk_index")
                if chunk_idx is None:
                    content = doc.get("content", "") or ""
                    # SHA1 used for chunk-id fingerprinting only, not security.
                    chunk_idx = _hashlib.sha1(
                        content.encode("utf-8"), usedforsecurity=False
                    ).hexdigest()[:32]
                key = f"{meta.get('meeting_id')}:{meta.get('file_id')}:{chunk_idx}"
            if key not in seen:
                seen.add(key)
                merged.append(doc)

    uniform = chunks_per_file if isinstance(chunks_per_file, int) else "adaptive"
    logger.info(
        "Fair retrieval: %d files x %s chunks/file -> %d unique chunks",
        len(scope_file_ids),
        uniform,
        len(merged),
    )
    return merged
