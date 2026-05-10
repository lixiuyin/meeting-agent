"""Text embedding service - supports multiple providers (OpenAI, Ollama, etc.)"""

import asyncio
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.embeddings import Embeddings
from pydantic import SecretStr

from ..core.config import settings

logger = logging.getLogger(__name__)

# Thread-safe singleton
_embeddings: Embeddings | None = None
_lock = threading.Lock()

# Some providers (e.g. OpenRouter) intermittently respond to embedding requests
# with an empty payload, surfaced by the LangChain OpenAI client as
# ``ValueError: No embedding data received``. The error is transient — retrying
# with a short backoff almost always succeeds — so we wrap embedding calls.
_EMBED_MAX_RETRIES = 2
_EMPTY_RESPONSE_MARKER = "No embedding data received"


def _is_empty_response_error(exc: BaseException) -> bool:
    return isinstance(exc, ValueError) and _EMPTY_RESPONSE_MARKER in str(exc)


def _retry_on_empty_sync[T](
    label: str, fn: Callable[[], T], *, max_retries: int = _EMBED_MAX_RETRIES
) -> T:
    """Run *fn* and retry transient ``No embedding data received`` errors."""
    # Guard: this function must not be called from an async context
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass  # No running loop, safe to proceed
    else:
        raise RuntimeError(
            "_retry_on_empty_sync called from async context — use _retry_on_empty_async instead"
        )
    last_exc: BaseException | None = None
    for attempt in range(max_retries):
        try:
            return fn()
        except ValueError as exc:
            if not _is_empty_response_error(exc):
                raise
            last_exc = exc
            if attempt < max_retries - 1:
                delay = min(float(attempt + 1), 2.0)
                logger.warning(
                    "%s: no data received (attempt %d/%d), retrying in %.1fs",
                    label,
                    attempt + 1,
                    max_retries,
                    delay,
                )
                time.sleep(delay)
    assert last_exc is not None
    raise last_exc


async def _retry_on_empty_async[T](
    label: str,
    fn: Callable[[], Awaitable[T]],
    *,
    max_retries: int = _EMBED_MAX_RETRIES,
) -> T:
    """Async variant of ``_retry_on_empty_sync``."""
    last_exc: BaseException | None = None
    for attempt in range(max_retries):
        try:
            return await fn()
        except ValueError as exc:
            if not _is_empty_response_error(exc):
                raise
            last_exc = exc
            if attempt < max_retries - 1:
                delay = float(attempt + 1)
                logger.warning(
                    "%s: no data received (attempt %d/%d), retrying in %.0fs",
                    label,
                    attempt + 1,
                    max_retries,
                    delay,
                )
                await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc


class _QueryCachedEmbeddings(Embeddings):
    """Wraps a LangChain Embeddings instance with an LRU cache on ``embed_query``
    AND a per-text cache on ``embed_documents``.

    Motivation: within a single pipeline turn the same query is embedded up to
    5 times (skill matcher, vector search, memories, session context, entity
    context). Even across turns, users often re-ask the same question. Caching
    at the wrapper level avoids duplicate embedding API calls without modifying
    downstream code.

    Document embeddings are also cached per-text so callers that batch-prewarm
    a set of texts (e.g. fact / entity extraction) and then later trigger
    single-document upserts internally don't pay the per-document HTTP cost
    again. ``vs.upsert`` flows that call ``add_documents([doc])`` reduce from
    N HTTP calls to a single batched prewarm + N cache hits.

    Uses a single ``threading.Lock`` for all cache mutations so both sync and
    async paths see a consistent view. Async methods delegate cache lookups
    via ``asyncio.to_thread`` so the event loop is never blocked.

    Includes stampede protection: concurrent requests for the same text
    coalesce into a single API call.
    """

    def __init__(
        self, inner: Embeddings, max_size: int, stampede_wait_s: float | None = None
    ) -> None:
        self._inner = inner
        self._max = max(1, max_size)
        # H-8: Configurable stampede wait timeout.  Followers waiting on a
        # leader must outlast the provider's worst-case call duration (e.g.
        # local HuggingFace cold start 10-30s, cloud p99 > 5s).  Falls back
        # to a provider-aware default when not explicitly set.
        self._stampede_wait_s = (
            stampede_wait_s if stampede_wait_s is not None else self._default_stampede_wait()
        )
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = threading.Lock()
        self._pending: dict[str, threading.Event] = {}

    @staticmethod
    def _default_stampede_wait() -> float:
        """Provider-aware default stampede wait timeout."""
        binding = settings.EMBEDDING_BINDING.lower()
        # Local providers (cold start can take 10-30s)
        if binding in ("huggingface", "ollama", "lm_studio", "vllm", "llama_cpp"):
            return 45.0
        # Cloud providers (p99 typically < 10s)
        return 15.0

    def _cache_get(self, text: str) -> list[float] | None:
        with self._lock:
            hit = self._cache.get(text)
            if hit is not None:
                self._cache.move_to_end(text)
                try:
                    from ..core.metrics import EMBEDDER_CACHE_HIT_TOTAL

                    EMBEDDER_CACHE_HIT_TOTAL.inc()
                except Exception:
                    pass  # metrics are optional
            else:
                try:
                    from ..core.metrics import EMBEDDER_CACHE_MISS_TOTAL

                    EMBEDDER_CACHE_MISS_TOTAL.inc()
                except Exception:
                    pass  # metrics are optional
            return hit

    def _cache_put(self, text: str, result: list[float]) -> None:
        with self._lock:
            self._cache[text] = result
            self._cache.move_to_end(text)
            while len(self._cache) > self._max:
                self._cache.popitem(last=False)
            ev = self._pending.pop(text, None)
        if ev is not None:
            ev.set()

    def _get_pending_event(self, text: str) -> threading.Event | None:
        with self._lock:
            if text in self._pending:
                return self._pending[text]
            self._pending[text] = threading.Event()
            return None

    def _remove_pending(self, text: str) -> None:
        with self._lock:
            ev = self._pending.pop(text, None)
        if ev is not None:
            ev.set()

    def embed_query(self, text: str) -> list[float]:
        hit = self._cache_get(text)
        if hit is not None:
            return hit
        pending = self._get_pending_event(text)
        if pending is not None:
            pending.wait(timeout=self._stampede_wait_s)
            hit = self._cache_get(text)
            if hit is not None:
                return hit
        try:
            result = _retry_on_empty_sync("embed_query", lambda: self._inner.embed_query(text))
            self._cache_put(text, result)
            return result
        except Exception:
            self._remove_pending(text)
            raise

    async def aembed_query(self, text: str) -> list[float]:
        hit = await asyncio.to_thread(self._cache_get, text)
        if hit is not None:
            return hit
        pending = await asyncio.to_thread(self._get_pending_event, text)
        if pending is not None:
            await asyncio.to_thread(pending.wait, self._stampede_wait_s)
            hit = await asyncio.to_thread(self._cache_get, text)
            if hit is not None:
                return hit
        try:
            result = await _retry_on_empty_async(
                "aembed_query", lambda: self._inner.aembed_query(text)
            )
            await asyncio.to_thread(self._cache_put, text, result)
            return result
        except Exception:
            await asyncio.to_thread(self._remove_pending, text)
            raise

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of documents, hitting the per-text LRU cache when possible.

        Cache hits return immediately. Cache misses are coalesced into a single
        batched API call. Result order matches input order.

        Important: this means a batched prewarm followed by N single-text calls
        produces 1 HTTP call total instead of 1 + N. Callers in extraction flows
        that previously triggered N single-text upserts now amortize the cost.
        """
        if not texts:
            return []
        # Phase 1: cache lookup. Track misses and their original positions.
        results: list[list[float] | None] = [None] * len(texts)
        miss_positions: list[int] = []
        miss_texts: list[str] = []
        for i, text in enumerate(texts):
            hit = self._cache_get(text)
            if hit is not None:
                results[i] = hit
            else:
                miss_positions.append(i)
                miss_texts.append(text)

        if not miss_texts:
            return [r for r in results if r is not None]  # type: ignore[misc]

        # Phase 2: single batched API call for all cache misses.
        fresh = _retry_on_empty_sync(
            "embed_documents", lambda: self._inner.embed_documents(miss_texts)
        )
        if len(fresh) != len(miss_texts):
            logger.warning(
                "embed_documents: expected %d embeddings, got %d — some texts dropped",
                len(miss_texts),
                len(fresh),
            )
        # Phase 3: cache new results and splice into the result list.
        for pos, text, vec in zip(miss_positions, miss_texts, fresh, strict=False):
            self._cache_put(text, vec)
            results[pos] = vec
        return [r for r in results if r is not None]  # type: ignore[misc]

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        results: list[list[float] | None] = [None] * len(texts)
        miss_positions: list[int] = []
        miss_texts: list[str] = []
        for i, text in enumerate(texts):
            hit = await asyncio.to_thread(self._cache_get, text)
            if hit is not None:
                results[i] = hit
            else:
                miss_positions.append(i)
                miss_texts.append(text)

        if not miss_texts:
            return [r for r in results if r is not None]  # type: ignore[misc]

        fresh = await _retry_on_empty_async(
            "aembed_documents", lambda: self._inner.aembed_documents(miss_texts)
        )
        if len(fresh) != len(miss_texts):
            logger.warning(
                "aembed_documents: expected %d embeddings, got %d — some texts dropped",
                len(miss_texts),
                len(fresh),
            )
        for pos, text, vec in zip(miss_positions, miss_texts, fresh, strict=False):
            await asyncio.to_thread(self._cache_put, text, vec)
            results[pos] = vec
        return [r for r in results if r is not None]  # type: ignore[misc]

    def __getattr__(self, name: str) -> Any:
        # Forward any provider-specific attributes (model, base_url, etc.)
        # that downstream code might read for telemetry.
        return getattr(self._inner, name)


def _init_embeddings(cls: Any, **kwargs: Any) -> Embeddings:
    """Initialize an embeddings provider.

    Centralizes type-ignore for third-party constructor stub mismatches
    (missing params, wrong return type annotations).
    """
    return cls(**kwargs)  # type: ignore[call-arg,return-value,arg-type]


def _create_openai_embeddings() -> Embeddings:
    """Create OpenAI-compatible embeddings instance"""
    from langchain_openai import OpenAIEmbeddings

    kwargs: dict[str, Any] = {"model": settings.EMBEDDING_MODEL}

    api_key = (
        settings.EMBEDDING_API_KEY.get_secret_value() or settings.LLM_API_KEY.get_secret_value()
    )
    base_url = settings.EMBEDDING_BASE_URL or settings.LLM_BASE_URL

    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url

    return _init_embeddings(OpenAIEmbeddings, **kwargs)


def _create_azure_openai_embeddings() -> Embeddings:
    """Create Azure OpenAI embeddings instance"""
    from langchain_openai import AzureOpenAIEmbeddings

    kwargs: dict[str, Any] = {"model": settings.EMBEDDING_MODEL}

    embed_key = settings.EMBEDDING_API_KEY.get_secret_value()
    if embed_key:
        kwargs["api_key"] = embed_key
    if settings.EMBEDDING_BASE_URL:
        kwargs["azure_endpoint"] = settings.EMBEDDING_BASE_URL

    return _init_embeddings(AzureOpenAIEmbeddings, **kwargs)


def _create_ollama_embeddings() -> Embeddings:
    """Create Ollama local embeddings instance"""
    from langchain_ollama import OllamaEmbeddings

    base_url = settings.EMBEDDING_HOST or settings.EMBEDDING_BASE_URL or "http://localhost:11434"

    return _init_embeddings(
        OllamaEmbeddings,
        model=settings.EMBEDDING_MODEL,
        base_url=base_url,
    )


def _create_lm_studio_embeddings() -> Embeddings:
    """Create LM Studio local embeddings instance (OpenAI-compatible)"""
    from langchain_openai import OpenAIEmbeddings

    base_url = settings.EMBEDDING_HOST or settings.EMBEDDING_BASE_URL or "http://localhost:1234/v1"

    return _init_embeddings(
        OpenAIEmbeddings,
        model=settings.EMBEDDING_MODEL,
        api_key=SecretStr("not-needed"),  # LM Studio doesn't require API key
        base_url=base_url,
    )


def _create_huggingface_embeddings() -> Embeddings:
    """Create HuggingFace embeddings instance.

    `langchain-huggingface` is an optional extra because it pulls
    sentence-transformers → torch (~3 GB on Linux CUDA). Install with:
        uv sync --extra huggingface
    """
    try:
        from langchain_huggingface import (  # type: ignore[import-not-found]
            HuggingFaceEmbeddings,
        )
    except ImportError as exc:
        raise RuntimeError(
            "EMBEDDING_BINDING=huggingface requires the optional 'huggingface' "
            "extra. Install it with: uv sync --extra huggingface"
        ) from exc

    return _init_embeddings(
        HuggingFaceEmbeddings,
        model_name=settings.EMBEDDING_MODEL,
    )


def _create_jina_embeddings() -> Embeddings:
    """Create Jina AI embeddings instance"""
    from langchain_community.embeddings import JinaEmbeddings  # type: ignore[import-not-found]

    api_key = (
        settings.EMBEDDING_API_KEY.get_secret_value() or settings.LLM_API_KEY.get_secret_value()
    )
    if not api_key:
        raise ValueError("Jina embeddings requires API key")

    return _init_embeddings(
        JinaEmbeddings,
        model=settings.EMBEDDING_MODEL,
        jina_api_key=api_key,
    )


def _create_cohere_embeddings() -> Embeddings:
    """Create Cohere embeddings instance"""
    from langchain_cohere import CohereEmbeddings  # type: ignore[import-not-found]

    api_key = (
        settings.EMBEDDING_API_KEY.get_secret_value() or settings.LLM_API_KEY.get_secret_value()
    )
    if not api_key:
        raise ValueError("Cohere embeddings requires API key")

    return _init_embeddings(
        CohereEmbeddings,
        model=settings.EMBEDDING_MODEL,
        cohere_api_key=api_key,
    )


def _create_google_embeddings() -> Embeddings:
    """Create Google Vertex AI embeddings instance"""
    from langchain_google_vertexai import VertexAIEmbeddings  # type: ignore[import-not-found]

    return _init_embeddings(
        VertexAIEmbeddings,
        model_name=settings.EMBEDDING_MODEL,
    )


def _create_openrouter_embeddings() -> Embeddings:
    """Create OpenRouter embeddings instance (OpenAI-compatible)."""
    from langchain_openai import OpenAIEmbeddings

    kwargs: dict[str, Any] = {"model": settings.EMBEDDING_MODEL}

    api_key = (
        settings.EMBEDDING_API_KEY.get_secret_value() or settings.LLM_API_KEY.get_secret_value()
    )
    if not api_key:
        raise ValueError(
            "OpenRouter embeddings requires API key. Set EMBEDDING_API_KEY or LLM_API_KEY."
        )

    base_url = (
        settings.EMBEDDING_BASE_URL or settings.LLM_BASE_URL or "https://openrouter.ai/api/v1"
    )
    kwargs["api_key"] = api_key
    kwargs["base_url"] = base_url

    return _init_embeddings(OpenAIEmbeddings, **kwargs)


def get_embeddings() -> Embeddings:
    """Get or create the singleton embeddings instance (thread-safe)"""
    global _embeddings
    if _embeddings is None:
        with _lock:
            if _embeddings is None:
                binding = settings.EMBEDDING_BINDING.lower()

                creators = {
                    "openai": _create_openai_embeddings,
                    "azure_openai": _create_azure_openai_embeddings,
                    "ollama": _create_ollama_embeddings,
                    "lm_studio": _create_lm_studio_embeddings,
                    "huggingface": _create_huggingface_embeddings,
                    "jina": _create_jina_embeddings,
                    "cohere": _create_cohere_embeddings,
                    "google": _create_google_embeddings,
                    # OpenAI-compatible API providers
                    "openrouter": _create_openrouter_embeddings,
                    "deepseek": _create_openai_embeddings,
                    "together": _create_openai_embeddings,
                    "groq": _create_openai_embeddings,
                    "mistral": _create_openai_embeddings,
                    "vllm": _create_openai_embeddings,
                }

                if binding not in creators:
                    raise ValueError(
                        f"Unsupported embedding binding: {settings.EMBEDDING_BINDING}. "
                        f"Supported: {', '.join(creators.keys())}"
                    )

                raw = creators[binding]()
                if settings.EMBEDDING_QUERY_CACHE_ENABLED:
                    stampede_wait = settings.EMBEDDING_STAMPEDE_WAIT_S or None
                    new_embeddings = _QueryCachedEmbeddings(
                        raw,
                        max_size=settings.EMBEDDING_QUERY_CACHE_SIZE,
                        stampede_wait_s=stampede_wait,
                    )
                else:
                    new_embeddings = raw
                # Assign as last step so partial init is never visible to other threads.
                _embeddings = new_embeddings
                logger.info(
                    "Initialized %s embeddings with model %s (query_cache=%s)",
                    binding,
                    settings.EMBEDDING_MODEL,
                    settings.EMBEDDING_QUERY_CACHE_ENABLED,
                )

    return _embeddings


def reset_embeddings() -> None:
    """Reset the embeddings singleton so the next call creates a fresh instance."""
    global _embeddings
    with _lock:
        _embeddings = None
    logger.info("Embeddings singleton reset")


def get_embedding_dimension() -> int:
    """Get the configured embedding dimension"""
    return settings.EMBEDDING_DIMENSION
