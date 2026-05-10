"""Document reranking: Cohere API and BGE cross-encoder."""

import logging
import threading
from typing import Any

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ...core.config import settings
from ...core.metrics import (
    RERANKER_FAILURE_TOTAL,
    RERANKER_LOW_QUALITY_FALLBACK_TOTAL,
    RERANKER_REQUESTS_TOTAL,
)

logger = logging.getLogger(__name__)


_rerank_retry = retry(
    retry=retry_if_exception_type((ConnectionError, TimeoutError)),
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)

# Reranker model singleton (BGE cross-encoder)
_reranker_model: Any = None
_reranker_lock = threading.Lock()

# Cohere client singleton
_cohere_client: Any = None
_cohere_client_lock = threading.Lock()
_cohere_client_key: str | None = None

# Shared async httpx client for Cohere HTTP reranking — avoids creating
# a new client per call and uses the event loop's native I/O instead of
# blocking a thread-pool thread.
_reranker_http_client: Any = None
_reranker_http_client_lock = threading.Lock()


def reset_reranker_state() -> None:
    """Reset reranker singletons so updated settings apply deterministically.

    Holds all locks simultaneously so readers don't see a half-reset state
    (e.g. BGE model cleared but Cohere client still pointing at old creds).
    Explicitly deletes the BGE model and frees GPU memory when possible.
    """
    global _reranker_model, _cohere_client, _cohere_client_key, _reranker_http_client
    with _reranker_lock, _cohere_client_lock, _reranker_http_client_lock:
        if _reranker_model is not None:
            del _reranker_model
            _reranker_model = None
            try:
                import torch

                if torch.cuda.is_available():  # type: ignore[union-attr]
                    torch.cuda.empty_cache()  # type: ignore[union-attr]
            except Exception:
                pass  # torch is optional; cache cleanup is best-effort
        _cohere_client = None
        _cohere_client_key = None
        _reranker_http_client = None
    logger.info("Reranker singleton state reset")


def _stable_key(doc: dict) -> str:
    """Return a stable identity key for dedup, avoiding Python object id().

    Uses chunk_id from metadata when available; falls back to a composite of
    meeting_id, file_id, and chunk_index.  When all identifiers are missing
    (e.g. synthetic summary docs), falls back to id(doc) so each doc gets its
    own slot instead of all colliding on ``None:None:None``.
    """
    meta = doc.get("metadata") or {}
    cid = meta.get("chunk_id")
    if cid:
        return str(cid)
    mid = meta.get("meeting_id")
    fid = meta.get("file_id")
    cidx = meta.get("chunk_index")
    if mid is not None or fid is not None or cidx is not None:
        return f"{mid}:{fid}:{cidx}"
    return f"_doc_{id(doc)}"


def _apply_per_file_guarantee(
    ranked: list[dict],
    top_n: int,
    min_per_file: int,
) -> list[dict]:
    """Force-keep at least ``min_per_file`` chunks per file_id, then fill by score.

    ``ranked`` must be sorted by relevance (best first).  When the number of
    distinct files exceeds ``top_n``, the result is enlarged to fit every file
    so no file gets dropped — the caller is responsible for any subsequent
    truncation that respects the per-file guarantee.
    """
    if not ranked or min_per_file <= 0:
        return ranked[:top_n]

    per_file: dict[int | None, list[dict]] = {}
    for doc in ranked:
        meta = doc.get("metadata")
        fid = meta.get("file_id") if isinstance(meta, dict) else None
        per_file.setdefault(fid, []).append(doc)

    guaranteed: list[dict] = []
    guaranteed_keys: set[str] = set()

    # First pass: take top ``min_per_file`` from each file.
    for file_docs in per_file.values():
        for doc in file_docs[:min_per_file]:
            key = _stable_key(doc)
            if key not in guaranteed_keys:
                guaranteed_keys.add(key)
                guaranteed.append(doc)

    # Effective budget: never below the count of distinct files (so every file
    # keeps its guaranteed share even when ``top_n`` is small).
    effective_n = max(top_n, len(guaranteed))

    # Fill remaining slots by global rank order.
    if len(guaranteed) < effective_n:
        for doc in ranked:
            key = _stable_key(doc)
            if key not in guaranteed_keys:
                guaranteed.append(doc)
                guaranteed_keys.add(key)
                if len(guaranteed) >= effective_n:
                    break

    return guaranteed[:effective_n]


def rerank(
    query: str,
    docs: list[dict],
    top_n: int | None = None,
    *,
    is_unscoped: bool = False,
    min_per_file: int = 0,
) -> list[dict]:
    """Rerank documents using configured reranker backend.

    Args:
        query: the user's question
        docs: list of retrieved documents
        top_n: number of documents to return after reranking
        is_unscoped: when True, uses a softer score threshold to avoid
            cutting broad low-score results entirely.
        min_per_file: when > 0, guarantee that each file_id has at least
            this many docs in the final output before filling by global score.

    Returns:
        Reranked and truncated list of documents, filtered by RERANKER_MIN_SCORE.
    """
    binding = settings.RERANKER_BINDING.lower()
    if not binding or not docs:
        return [{**d, "reranked": False} for d in docs]
    top_n = top_n or settings.RERANKER_TOP_N
    # Scale reranker output proportionally when the fetch multiplier pulled
    # many candidates.  Without this, wide-recall investment is wasted because
    # the reranker returns a fixed top_n regardless of candidate pool size.
    oversample_ratio = len(docs) / max(top_n, 1)
    if oversample_ratio >= 1.5:
        scaled_top_n = min(len(docs), max(top_n, int(top_n * oversample_ratio * 0.5)))
    else:
        scaled_top_n = top_n
    # When per-file guarantee is requested, score every candidate so that no
    # file is silently dropped at the reranker boundary (Cohere/BGE truncate
    # to ``rerank_top_n`` by default, which would hide files that were not in
    # the top-N globally).
    rerank_pool_n = max(scaled_top_n, len(docs)) if min_per_file > 0 else scaled_top_n
    if binding == "cohere":
        RERANKER_REQUESTS_TOTAL.labels(backend="cohere").inc()
        ranked = _rerank_cohere(query, docs, rerank_pool_n)
    elif binding == "bge":
        RERANKER_REQUESTS_TOTAL.labels(backend="bge").inc()
        ranked = _rerank_bge(query, docs, rerank_pool_n)
    else:
        logger.warning("Unknown reranker binding: %s, skipping", binding)
        return [{**d, "reranked": False} for d in docs]
    # MEDIUM-4: Dual min_score thresholds for scoped vs unscoped.
    if is_unscoped:
        min_score = settings.RERANKER_UNSCOPED_MIN_SCORE
    else:
        min_score = settings.RERANKER_SCOPED_MIN_SCORE
    if min_score > 0:
        filtered = [d for d in ranked if d.get("score", 0) >= min_score]
        if len(filtered) < len(ranked):
            logger.debug(
                "Reranker score cutoff: %d -> %d docs (min_score=%.2f)",
                len(ranked),
                len(filtered),
                min_score,
            )
        # Guarantee at least top_n results even if all scores are below threshold
        if not filtered and ranked:
            filtered = ranked[:top_n]
            RERANKER_LOW_QUALITY_FALLBACK_TOTAL.labels(collection="default").inc()
            logger.info(
                "Reranker low-quality fallback: all %d docs below min_score=%.2f",
                len(ranked),
                min_score,
            )
        ranked = filtered

    # Per-file guarantee: force-keep top-N per file, then fill by score
    if min_per_file > 0:
        ranked = _apply_per_file_guarantee(ranked, top_n, min_per_file)

    return ranked


def _get_cohere_client(api_key: str) -> Any:
    """Get or create the singleton Cohere client (thread-safe)."""
    global _cohere_client, _cohere_client_key
    if _cohere_client is not None and _cohere_client_key == api_key:
        return _cohere_client
    with _cohere_client_lock:
        if _cohere_client is not None and _cohere_client_key == api_key:
            return _cohere_client
        try:
            import cohere

            _cohere_client = cohere.ClientV2(api_key=api_key)
            _cohere_client_key = api_key
        except ImportError:
            logger.error("cohere library not installed; pip install cohere")
            return None
    return _cohere_client


def _rerank_cohere(query: str, docs: list[dict], top_n: int) -> list[dict]:
    """Rerank using Cohere API (or OpenRouter-compatible endpoint).

    M-2: Batches documents when the candidate pool exceeds ``_RERANK_BATCH_SIZE``
    to avoid hitting per-request limits. Each batch is retried with exponential
    backoff on transient failures.
    """
    model = settings.RERANKER_MODEL or "cohere/rerank-4-pro"
    base_url = settings.RERANKER_BASE_URL
    api_key = (
        settings.RERANKER_API_KEY.get_secret_value() or settings.LLM_API_KEY.get_secret_value()
    )
    if not api_key:
        logger.warning(
            "Reranker disabled: neither RERANKER_API_KEY nor LLM_API_KEY is set "
            "(binding=cohere, model=%s)",
            model,
        )
        return [{**d, "reranked": False} for d in docs]
    if base_url:
        return _rerank_cohere_http(query, docs, top_n, api_key, base_url, model)
    client = _get_cohere_client(api_key)
    if client is None:
        return [{**d, "reranked": False} for d in docs]

    # M-2: Batch processing for large candidate pools
    batch_size = settings.RERANKER_BATCH_SIZE
    if len(docs) <= batch_size:
        return _rerank_cohere_batch(client, model, query, docs, top_n)

    all_ranked: list[dict] = []
    for start in range(0, len(docs), batch_size):
        batch = docs[start : start + batch_size]
        batch_top_n = min(top_n, len(batch))
        ranked = _rerank_cohere_batch(client, model, query, batch, batch_top_n)
        all_ranked.extend(ranked)
    # Re-sort by score and truncate to top_n
    all_ranked.sort(key=lambda d: d.get("score", 0), reverse=True)
    return all_ranked[:top_n]


def _rerank_cohere_batch(
    client: Any, model: str, query: str, docs: list[dict], top_n: int
) -> list[dict]:
    """Rerank a single batch via the Cohere SDK with retry."""
    documents = [doc["content"] for doc in docs]
    try:

        @_rerank_retry
        def _call():
            return client.rerank(
                model=model,
                query=query,
                documents=documents,
                top_n=top_n,
            )

        response = _call()
        return [
            {**docs[r.index], "score": r.relevance_score, "reranked": True}
            for r in response.results
        ]
    except Exception:
        RERANKER_FAILURE_TOTAL.labels(backend="cohere").inc()
        logger.error("Cohere rerank failed", exc_info=True)
        return [{**d, "reranked": False} for d in docs]


def _get_reranker_http_client() -> Any:
    """Get or create a shared async httpx client for reranker HTTP calls."""
    global _reranker_http_client
    if _reranker_http_client is not None:
        return _reranker_http_client
    with _reranker_http_client_lock:
        if _reranker_http_client is not None:
            return _reranker_http_client
        import httpx

        _reranker_http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.RERANKER_TIMEOUT_SECONDS),
            limits=httpx.Limits(max_keepalive_connections=2, max_connections=10),
        )
        logger.debug("Reranker async HTTP client created")
    return _reranker_http_client


async def _close_reranker_http_client() -> None:
    """Close the shared reranker HTTP client (called during shutdown)."""
    global _reranker_http_client
    with _reranker_http_client_lock:
        client = _reranker_http_client
        _reranker_http_client = None
    if client is not None:
        await client.aclose()


def _rerank_cohere_http(
    query: str,
    docs: list[dict],
    top_n: int,
    api_key: str,
    base_url: str,
    model: str,
) -> list[dict]:
    """Rerank using a custom base URL via direct HTTP (e.g., OpenRouter).

    Uses sync ``httpx.post()`` because this function is always invoked from
    within ``asyncio.to_thread()`` — the thread pool handles concurrency and
    blocking on sync I/O is safe here.

    M-2: Retries transient HTTP errors with exponential backoff.
    """
    import httpx

    url = base_url.rstrip("/") + "/rerank"
    documents = [doc["content"] for doc in docs]
    try:

        @_rerank_retry
        def _call():
            resp = httpx.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "query": query,
                    "documents": documents,
                    "top_n": top_n,
                },
                timeout=settings.RERANKER_TIMEOUT_SECONDS,
                trust_env=False,
            )
            resp.raise_for_status()
            return resp.json()

        data = _call()
        return [
            {**docs[r["index"]], "score": r["relevance_score"], "reranked": True}
            for r in data["results"]
        ]
    except Exception:
        RERANKER_FAILURE_TOTAL.labels(backend="cohere_http").inc()
        logger.error("Cohere rerank HTTP failed", exc_info=True)
        return [{**d, "reranked": False} for d in docs]


def _get_reranker_model() -> Any:
    """Get or create the singleton BGE reranker model (thread-safe)."""
    global _reranker_model
    if _reranker_model is None:
        with _reranker_lock:
            if _reranker_model is None:
                try:
                    from sentence_transformers import CrossEncoder

                    _reranker_model = CrossEncoder("BAAI/bge-reranker-v2-m3")
                    logger.info("BGE reranker model loaded")
                except ImportError:
                    logger.error(
                        "RERANKER_BINDING=bge requires the optional 'huggingface' "
                        "extra (sentence-transformers). Install with: "
                        "uv sync --extra huggingface"
                    )
                    return None
    return _reranker_model


def _rerank_bge(query: str, docs: list[dict], top_n: int) -> list[dict]:
    """Rerank using local BGE cross-encoder (singleton model)."""
    model = _get_reranker_model()
    if model is None:
        return [{**d, "reranked": False} for d in docs]
    pairs = [(query, doc["content"]) for doc in docs]
    scores = model.predict(pairs)
    ranked = sorted(zip(scores, docs, strict=False), key=lambda x: x[0], reverse=True)
    return [{**doc, "score": float(score), "reranked": True} for score, doc in ranked[:top_n]]
