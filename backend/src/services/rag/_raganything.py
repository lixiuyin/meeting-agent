"""RAGAnything bridge for optional multimodal retrieval."""

from __future__ import annotations

import asyncio
import atexit
import inspect
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from ...core._config_snapshot import submit_with_context
from ...core.config import settings
from ...core.constants import DATA_DIR
from ..embedder import get_embeddings
from ..llm import cached_retry_invoke, get_llm
from ..parser.types import ParsedDocument

logger = logging.getLogger(__name__)

_raganything_singleton: Any | None = None
_raganything_key: tuple[str, int, int, int] | None = None
_raganything_lock = threading.Lock()
_raganything_index_lock = threading.Lock()
_raganything_types_cache: tuple[Any, Any] | None = None
_DOC_ID_PATTERN = re.compile(r"meeting_(?P<meeting>\d+)_file_(?P<file>[^_\s]+)")
_SCOPE_PREAMBLE_PATTERN = re.compile(
    r"\[SCOPE meeting_id=(?P<meeting>\d+) file_id=(?P<file>\d+|unknown) doc_id=(?P<doc>[^\]\s]+)\]"
)
_PRINCIPAL_PREAMBLE_PATTERN = re.compile(r"\[PRINCIPAL user_id=(?P<user>[^\]\s]+)\]")
_ASYNC_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="raganything")


def _shutdown_executor() -> None:
    _ASYNC_EXECUTOR.shutdown(wait=False, cancel_futures=True)


atexit.register(_shutdown_executor)


def reset_raganything() -> None:
    """Reset the singleton so settings changes can re-create the client."""
    global _raganything_key, _raganything_singleton, _raganything_types_cache
    with _raganything_lock:
        _raganything_singleton = None
        _raganything_key = None
        _raganything_types_cache = None
    logger.info("RAGAnything singleton reset")


def _ensure_operation_succeeded(payload: Any, *, operation: str) -> Any:
    """Raise when provider payload reports an explicit operation failure."""
    if not isinstance(payload, dict):
        return payload

    success = payload.get("success")
    if success is False:
        message = payload.get("error") or payload.get("message") or "unknown error"
        raise RuntimeError(f"{operation} failed: {message}")

    status = str(payload.get("status", "")).strip().lower()
    if status and status not in {"success", "ok", "done", "completed"}:
        message = payload.get("error") or payload.get("message") or status
        raise RuntimeError(f"{operation} failed: {message}")
    return payload


def _run_async(coro: Any) -> Any:
    """Run coroutine from sync context, including when an event loop already runs."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    future = submit_with_context(_ASYNC_EXECUTOR, asyncio.run, coro)
    return future.result()


def _ctor_accepts_var_kwargs(func: Any) -> bool:
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return True
    return any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())


def _filter_kwargs(func: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    if _ctor_accepts_var_kwargs(func):
        return kwargs
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return kwargs
    allowed = set(sig.parameters.keys())
    return {k: v for k, v in kwargs.items() if k in allowed}


def is_raganything_available() -> bool:
    """Check whether the optional ``raganything`` package is installed."""
    try:
        import raganything  # type: ignore[import-not-found]  # noqa: F401

        return True
    except Exception:
        return False


def _import_raganything_types() -> tuple[Any, Any]:
    global _raganything_types_cache
    if _raganything_types_cache is not None:
        return _raganything_types_cache

    try:
        from raganything import RAGAnything  # type: ignore[import-not-found]
    except Exception as exc:
        raise RuntimeError(
            "RAGAnything is not available. Install the multimodal extra: uv sync --extra multimodal"
        ) from exc

    QueryParam = None
    try:
        from lightrag.base import QueryParam as _QueryParam  # type: ignore[import-not-found]

        QueryParam = _QueryParam
    except Exception:
        try:
            from raganything import QueryParam as _QueryParam  # type: ignore[import-not-found]

            QueryParam = _QueryParam
        except Exception:
            QueryParam = None
    _raganything_types_cache = (RAGAnything, QueryParam)
    return _raganything_types_cache


def _get_working_dir() -> Path:
    raw = settings.RAGANYTHING_WORKING_DIR.strip()
    if raw:
        return Path(raw)
    return DATA_DIR / "raganything"


def _build_llm_adapter() -> Any:
    llm = get_llm()

    async def _llm_model_func(prompt: str, **_kwargs: Any) -> str:
        response = await asyncio.wait_for(
            asyncio.to_thread(cached_retry_invoke, llm, prompt),
            timeout=settings.RAGANYTHING_LLM_TIMEOUT_SECONDS,
        )
        content = response.content if hasattr(response, "content") else response
        if isinstance(content, str):
            return content
        return str(content)

    return _llm_model_func


def _build_embedding_adapter() -> Any:
    embeddings = get_embeddings()

    async def _embedding_func(texts: list[str]) -> Any:
        result = await asyncio.to_thread(embeddings.embed_documents, texts)
        import numpy as np

        return np.array(result, dtype=np.float32)

    try:
        from lightrag.utils import EmbeddingFunc as _EmbeddingFunc  # type: ignore[import-not-found]
    except Exception:
        return _embedding_func

    return _EmbeddingFunc(
        embedding_dim=settings.EMBEDDING_DIMENSION,
        func=_embedding_func,
        max_token_size=8192,
    )


def _create_raganything() -> Any:
    RAGAnything, _ = _import_raganything_types()
    working_dir = _get_working_dir()
    working_dir.mkdir(parents=True, exist_ok=True)

    kwargs: dict[str, Any] = {
        "working_dir": str(working_dir),
        "llm_model_func": _build_llm_adapter(),
        "embedding_func": _build_embedding_adapter(),
    }
    ctor_kwargs = _filter_kwargs(RAGAnything, kwargs)
    return RAGAnything(**ctor_kwargs)


def _get_raganything() -> Any:
    """Get or create RAGAnything singleton."""
    global _raganything_key, _raganything_singleton
    config_key = (
        str(_get_working_dir().resolve()),
        id(get_llm()),
        id(get_embeddings()),
        settings.EMBEDDING_DIMENSION,
    )
    if _raganything_singleton is None or _raganything_key != config_key:
        with _raganything_lock:
            if _raganything_singleton is None or _raganything_key != config_key:
                _raganything_singleton = _create_raganything()
                _raganything_key = config_key
                logger.info("Initialized RAGAnything singleton")
    return _raganything_singleton


def _doc_id(meeting_id: int, file_id: int | None) -> str:
    return f"meeting_{meeting_id}_file_{file_id if file_id is not None else 'unknown'}"


def _scope_preamble(meeting_id: int, file_id: int | None, doc_id: str) -> str:
    file_tag = str(file_id) if file_id is not None else "unknown"
    return f"[SCOPE meeting_id={meeting_id} file_id={file_tag} doc_id={doc_id}]\n\n"


def _principal_preamble(user_id: str | None) -> str:
    """Embed an ownership hint while DB ownership remains authoritative."""
    if not user_id or user_id == "default":
        return ""
    normalized = re.sub(r"[\]\s]+", "_", user_id)
    return f"[PRINCIPAL user_id={normalized}]\n\n"


def _extract_text(parsed: ParsedDocument | None, text: str | None) -> str:
    if text and text.strip():
        return text.strip()
    if parsed is None:
        return ""
    return parsed.to_text().strip()


async def _call_best_effort_async_method(
    obj: Any,
    names: tuple[str, ...],
    *,
    kwargs: dict[str, Any],
) -> Any:
    last_error: Exception | None = None
    for name in names:
        method = getattr(obj, name, None)
        if method is None:
            continue
        try:
            call_kwargs = _filter_kwargs(method, kwargs)
            result = method(**call_kwargs)
            if inspect.isawaitable(result):
                return await result
            return result
        except TypeError:
            continue
        except Exception as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"None of methods {names!r} exist on {type(obj).__name__}")


def index_with_raganything(
    meeting_id: int,
    file_id: int | None,
    parsed: ParsedDocument | None = None,
    text: str | None = None,
    file_path: str | None = None,
    metadata: dict | None = None,
) -> None:
    """Index file content into RAGAnything store."""
    content = _extract_text(parsed, text)
    if not content:
        return

    doc_id = _doc_id(meeting_id, file_id)
    base_meta = dict(metadata or {})
    base_meta.update(
        {
            "meeting_id": meeting_id,
            "file_id": file_id,
            "doc_id": doc_id,
        }
    )
    if file_path:
        base_meta["file_path"] = file_path
    user_id = str(base_meta.get("user_id") or "default")
    wrapped_content = (
        _scope_preamble(meeting_id, file_id, doc_id) + _principal_preamble(user_id) + content
    )

    rag = _get_raganything()

    async def _index() -> None:
        payload = await asyncio.wait_for(
            _call_best_effort_async_method(
                rag,
                ("insert_content_list",),
                kwargs={
                    "content_list": [
                        {
                            "type": "text",
                            "text": wrapped_content,
                            "page_idx": 0,
                            "metadata": base_meta,
                        }
                    ],
                    "file_path": file_path or doc_id,
                    "doc_id": doc_id,
                },
            ),
            timeout=settings.RAGANYTHING_INDEX_TIMEOUT_SECONDS,
        )
        _ensure_operation_succeeded(payload, operation="index")

    with _raganything_index_lock:
        _run_async(_index())


def index_file_with_raganything(
    *,
    meeting_id: int,
    file_id: int | None,
    file_path: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Index an original file via multimodal document-processing APIs when available."""
    doc_id = _doc_id(meeting_id, file_id)
    base_meta = dict(metadata or {})
    base_meta.update(
        {
            "meeting_id": meeting_id,
            "file_id": file_id,
            "doc_id": doc_id,
            "file_path": file_path,
        }
    )

    rag = _get_raganything()
    output_dir = _get_working_dir() / doc_id
    output_dir.mkdir(parents=True, exist_ok=True)

    async def _index_file() -> None:
        payload = await asyncio.wait_for(
            _call_best_effort_async_method(
                rag,
                (
                    "aprocess_document_complete",
                    "aprocess_document",
                    "process_document_complete",
                    "process_document",
                ),
                kwargs={
                    "file_path": file_path,
                    "path": file_path,
                    "doc_id": doc_id,
                    "ids": [doc_id],
                    "metadata": base_meta,
                    "parse_method": "auto",
                    "output_dir": str(output_dir),
                },
            ),
            timeout=settings.RAGANYTHING_INDEX_TIMEOUT_SECONDS,
        )
        _ensure_operation_succeeded(payload, operation="index_file")

    with _raganything_index_lock:
        _run_async(_index_file())


def _extract_doc_id_from_scope_preamble(content: str) -> str | None:
    match = _SCOPE_PREAMBLE_PATTERN.search(content)
    if not match:
        return None
    return match.group("doc")


def _extract_doc_id(candidate: dict[str, Any]) -> str | None:
    metadata = candidate.get("metadata")
    if isinstance(metadata, dict):
        for key in ("doc_id", "document_id", "source_id"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    content = candidate.get("content")
    if isinstance(content, str):
        from_preamble = _extract_doc_id_from_scope_preamble(content)
        if from_preamble:
            return from_preamble
        match = _DOC_ID_PATTERN.search(content)
        if match:
            return match.group(0)
    return None


def _parse_result_item(item: Any) -> dict[str, Any] | None:
    if isinstance(item, dict):
        content = item.get("content") or item.get("text") or item.get("chunk")
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        score_raw = item.get("score", 0.0)
        if not isinstance(content, str) or not content.strip():
            return None
        try:
            score = float(score_raw)
        except (TypeError, ValueError):
            score = 0.0
        return {
            "content": content.strip(),
            "metadata": metadata,
            "score": score,
            "score_kind": "relevance",
        }
    if isinstance(item, str) and item.strip():
        return {
            "content": item.strip(),
            "metadata": {},
            "score": 0.0,
            "score_kind": "relevance",
        }
    return None


def _normalize_query_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        out = []
        for item in payload:
            parsed = _parse_result_item(item)
            if parsed is not None:
                out.append(parsed)
        return out

    if isinstance(payload, dict):
        normalized = _ensure_operation_succeeded(payload, operation="query")
        for key in ("chunks", "results", "context", "data"):
            value = normalized.get(key)
            if isinstance(value, list):
                return _normalize_query_payload(value)
        if "content" in normalized or "text" in normalized:
            single = _parse_result_item(normalized)
            return [single] if single else []

    if isinstance(payload, str) and payload.strip():
        return [
            {
                "content": payload.strip(),
                "metadata": {},
                "score": 0.0,
                "score_kind": "relevance",
            }
        ]
    return []


def _extract_scope_ids(filters: dict[str, Any]) -> tuple[set[int], set[int]]:
    meeting_ids: set[int] = set()
    file_ids: set[int] = set()
    clauses: list[dict[str, Any]] = []
    if filters:
        clauses.append(filters)
        and_clauses = filters.get("$and")
        if isinstance(and_clauses, list):
            clauses.extend([c for c in and_clauses if isinstance(c, dict)])
    for clause in clauses:
        for field, target in (("meeting_id", meeting_ids), ("file_id", file_ids)):
            value = clause.get(field)
            if isinstance(value, dict):
                in_values = value.get("$in")
                if isinstance(in_values, list):
                    for iv in in_values:
                        if isinstance(iv, int):
                            target.add(iv)
            elif isinstance(value, int):
                target.add(value)
    return meeting_ids, file_ids


def _lookup_scope_ids_by_doc_id(doc_id: str) -> tuple[int | None, int | None]:
    try:
        from ...core import database as db

        with db.get_connection() as conn:
            row = db.get_meeting_file_by_raganything_doc_id(conn, doc_id)
        if not row:
            return None, None
        meeting_raw = row.get("meeting_id")
        file_raw = row.get("id")
        meeting_id = meeting_raw if isinstance(meeting_raw, int) else None
        file_id = file_raw if isinstance(file_raw, int) else None
        return meeting_id, file_id
    except Exception:
        return None, None


def _lookup_user_id_by_doc_id(doc_id: str) -> str | None:
    """Resolve the authoritative owner for a multimodal document."""
    try:
        from ...core import database as db

        with db.get_connection() as conn:
            row = db.get_meeting_file_by_raganything_doc_id(conn, doc_id)
        if not row:
            return None
        value = row.get("user_id")
        return str(value) if value is not None else None
    except Exception:
        return None


def _scope_match(
    doc_id: str | None,
    meeting_ids: set[int],
    file_ids: set[int],
    user_id: str | None = None,
) -> bool:
    if not meeting_ids and not file_ids and not user_id:
        return True
    if not doc_id:
        return False

    match = _DOC_ID_PATTERN.search(doc_id)
    if not match:
        meeting_id, file_id = _lookup_scope_ids_by_doc_id(doc_id)
        if meeting_id is None:
            return False
        if meeting_ids and meeting_id not in meeting_ids:
            return False
        scope_matches = not (file_ids and (file_id is None or file_id not in file_ids))
        if not scope_matches:
            return False
        return not user_id or _lookup_user_id_by_doc_id(doc_id) == user_id

    meeting_id = int(match.group("meeting"))
    file_raw = match.group("file")
    file_id = int(file_raw) if file_raw.isdigit() else None

    if meeting_ids and meeting_id not in meeting_ids:
        return False
    if file_ids and file_id not in file_ids:
        return False
    return not user_id or _lookup_user_id_by_doc_id(doc_id) == user_id


def retrieve_with_raganything(
    query: str,
    *,
    top_k: int,
    filters: dict[str, Any],
) -> list[dict]:
    """Retrieve chunks via RAGAnything and normalize to native retriever format."""
    rag = _get_raganything()
    _, QueryParam = _import_raganything_types()

    async def _query() -> Any:
        if QueryParam is not None:
            params = QueryParam(mode="hybrid", top_k=top_k, only_need_context=True)
            return await asyncio.wait_for(
                rag.aquery(query, param=params),
                timeout=settings.RAGANYTHING_QUERY_TIMEOUT_SECONDS,
            )
        return await asyncio.wait_for(
            rag.aquery(query),
            timeout=settings.RAGANYTHING_QUERY_TIMEOUT_SECONDS,
        )

    payload = _run_async(_query())
    results = _normalize_query_payload(payload)
    if not results:
        return []

    meeting_ids, file_ids = _extract_scope_ids(filters)
    from ._filters import _extract_eq_filter

    user_id_raw = _extract_eq_filter(filters, "user_id")
    user_id = str(user_id_raw) if user_id_raw is not None else None
    if not meeting_ids and not file_ids and not user_id:
        return results[:top_k]

    filtered: list[dict[str, Any]] = []
    for item in results:
        doc_id = _extract_doc_id(item)
        if _scope_match(doc_id, meeting_ids, file_ids, user_id):
            filtered.append(item)
    return filtered[:top_k]
