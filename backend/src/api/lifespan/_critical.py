"""Critical startup path — app cannot function without these."""

import asyncio
import logging
from pathlib import Path

from ...core.config import settings

logger = logging.getLogger(__name__)


def init_sentry() -> None:
    """Initialize Sentry SDK if SENTRY_DSN is configured (optional)."""
    import os

    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        logger.debug("Sentry disabled (SENTRY_DSN not set)")
        return

    try:
        import sentry_sdk

        _SENSITIVE_FIELDS = frozenset(
            {
                "api_key",
                "llm_api_key",
                "assemblyai_api_key",
                "embedding_api_key",
                "reranker_api_key",
                "secret",
                "password",
                "token",
                "authorization",
            }
        )

        def _sanitize_sentry_event(event, hint):
            """Redact sensitive field values from Sentry events."""
            import re

            def _redact_dict(d):
                if not isinstance(d, dict):
                    return
                for key in list(d):
                    if isinstance(key, str) and key.lower() in _SENSITIVE_FIELDS:
                        d[key] = "[REDACTED]"
                    elif isinstance(d[key], dict):
                        _redact_dict(d[key])
                    elif isinstance(d[key], list):
                        for item in d[key]:
                            _redact_dict(item)

            _redact_dict(event)
            if "exception" in event:
                for exc in event["exception"].get("values", []):
                    val = exc.get("value", "")
                    if isinstance(val, str):
                        exc["value"] = re.sub(
                            r"(api_?key|secret|password|token)=[^\s,;)\]}]+",
                            r"\1=[REDACTED]",
                            val,
                            flags=re.IGNORECASE,
                        )
            return event

        def _sanitize_breadcrumb(crumb, hint):
            """Redact sensitive data from Sentry breadcrumbs."""
            import re

            if crumb.get("data"):
                for key in list(crumb["data"]):
                    if isinstance(key, str) and key.lower() in _SENSITIVE_FIELDS:
                        crumb["data"][key] = "[REDACTED]"
            message = crumb.get("message", "")
            if isinstance(message, str):
                crumb["message"] = re.sub(
                    r"(api_?key|secret|password|token)=[^\s,;)\]}]+",
                    r"\1=[REDACTED]",
                    message,
                    flags=re.IGNORECASE,
                )
            return crumb

        sentry_sdk.init(
            dsn=dsn,
            traces_sample_rate=0.1,
            environment=os.getenv("SENTRY_ENVIRONMENT", "dev"),
            send_default_pii=False,
            before_send=_sanitize_sentry_event,
            before_breadcrumb=_sanitize_breadcrumb,
        )
        logger.info("Sentry error tracking enabled")
    except ImportError:
        logger.debug("sentry-sdk not installed, skipping Sentry init")
    except Exception as exc:
        logger.warning("Sentry initialization failed: %s", exc, exc_info=True)


def run_alembic_upgrade() -> None:
    """Run Alembic migrations to bring the database schema up to date.

    Falls back to the legacy init_db() path if Alembic is not available.
    """
    try:
        from alembic.config import Config

        from alembic import command
    except ImportError:
        logger.warning("alembic not installed; using legacy init_db()")
        from ...core.database import init_db

        init_db()
        return

    backend_dir = Path(__file__).resolve().parent.parent.parent.parent
    alembic_ini = backend_dir / "alembic.ini"
    if not alembic_ini.exists():
        logger.warning("alembic.ini not found at %s; using legacy init_db()", alembic_ini)
        from ...core.database import init_db

        init_db()
        return

    alembic_cfg = Config(str(alembic_ini))
    try:
        command.upgrade(alembic_cfg, "head")
        try:
            from ...core.database import get_write_connection

            with get_write_connection() as conn:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            logger.warning("WAL checkpoint after migration failed", exc_info=True)
        # H3: verify both migration tracks agree — schema_version (legacy) and
        # alembic_version (Alembic) must both contain at least one row after upgrade.
        try:
            from ...core.database import get_connection

            with get_connection() as conn:
                sv = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
                av = conn.execute("SELECT COUNT(*) FROM alembic_version").fetchone()[0]
            if sv is None:
                logger.warning("schema_version table is empty (legacy migrations not applied)")
            if av == 0:
                logger.warning("alembic_version table is empty (Alembic migrations not applied)")
            if sv is not None and av > 0:
                logger.info(
                    "Migration consistency check passed: schema_version=%s, alembic rows=%s",
                    sv,
                    av,
                )
        except Exception:
            logger.warning("Migration consistency check failed", exc_info=True)

        logger.info("Alembic upgrade complete")
    except Exception as exc:
        logger.critical(
            "Alembic upgrade failed: %s. "
            "The database may need manual migration. "
            "Set MIGRATION_AUTO_UPGRADE=false to bypass and start degraded.",
            exc,
            exc_info=True,
        )
        raise RuntimeError(
            "Database migration failed. Fix the schema manually or set "
            "MIGRATION_AUTO_UPGRADE=false to skip migrations at startup."
        ) from exc


async def run_critical_startup() -> None:
    """Initialize critical startup dependencies required for core app behavior."""
    from ...core.tracing import setup_tracing
    from ...services.embedder import get_embeddings
    from ...services.llm import get_llm
    from ...services.rag import get_vectorstore
    from ...services.traffic_control import init_traffic_controller
    from ._shared import record_best_effort_failure

    setup_tracing()

    init_sentry()

    if settings.ENVIRONMENT != "dev" and not settings.API_KEY.get_secret_value():
        logger.critical(
            "API_KEY is empty while ENVIRONMENT=%s (non-dev). Refusing to start.",
            settings.ENVIRONMENT,
        )
        raise RuntimeError(
            f"API_KEY must be set when ENVIRONMENT={settings.ENVIRONMENT!r}. "
            "Set API_KEY in your .env file or environment variables."
        )

    try:
        await asyncio.wait_for(asyncio.to_thread(get_llm), timeout=30)
        logger.info("LLM singleton initialized")
    except TimeoutError:
        logger.warning("LLM initialization timed out after 30s; chat may fail on first use")
    except Exception as e:
        logger.warning("LLM initialization failed: %s; chat may fail on first use", e)

    init_traffic_controller()

    # Pre-initialize stream semaphore so concurrent requests never race on lazy init.
    try:
        from ...api.routers.chat import set_stream_semaphore

        set_stream_semaphore(asyncio.Semaphore(settings.STREAM_CONCURRENT_LIMIT))
        logger.info("Stream semaphore initialized (limit=%d)", settings.STREAM_CONCURRENT_LIMIT)
    except Exception:
        logger.warning("Stream semaphore pre-init failed (non-fatal)", exc_info=True)

    if settings.RERANKER_BINDING and settings.RERANKER_TOP_N < settings.TOP_K:
        logger.critical(
            "RERANKER_TOP_N (%d) < TOP_K (%d) — the reranker will silently "
            "drop chunks that retrieval worked to find. Set RERANKER_TOP_N >= TOP_K.",
            settings.RERANKER_TOP_N,
            settings.TOP_K,
        )
        raise RuntimeError(
            f"RERANKER_TOP_N ({settings.RERANKER_TOP_N}) must be >= TOP_K ({settings.TOP_K})"
        )

    _reranker_binding = settings.RERANKER_BINDING.lower()
    if _reranker_binding:
        _reranker_key = settings.RERANKER_API_KEY.get_secret_value()
        _llm_key = settings.LLM_API_KEY.get_secret_value()
        if not (_reranker_key or _llm_key):
            logger.warning(
                "Reranker binding is '%s' but no RERANKER_API_KEY or LLM_API_KEY is set. "
                "Reranking will be silently skipped. Set the key or set reranker_binding "
                "to empty string to disable.",
                _reranker_binding,
            )

    try:
        embeddings = await asyncio.wait_for(asyncio.to_thread(get_embeddings), timeout=30)
        logger.info("Embeddings singleton initialized")
    except TimeoutError:
        _retriever_needs_embeddings = settings.RAG_RETRIEVER_PROVIDER.lower() in (
            "native",
            "hybrid_multimodal",
        )
        if settings.ENVIRONMENT == "production" and _retriever_needs_embeddings:
            raise RuntimeError(
                "Embeddings initialization timed out in production with "
                f"RAG_RETRIEVER_PROVIDER={settings.RAG_RETRIEVER_PROVIDER!r}. "
                "This is a fatal startup error."
            ) from None
        logger.warning("Embeddings initialization timed out after 30s; vector ops may fail")
        embeddings = None
    except Exception as e:
        _retriever_needs_embeddings = settings.RAG_RETRIEVER_PROVIDER.lower() in (
            "native",
            "hybrid_multimodal",
        )
        if settings.ENVIRONMENT == "production" and _retriever_needs_embeddings:
            raise RuntimeError(f"Embeddings initialization failed in production: {e}") from e
        logger.warning("Embeddings initialization failed: %s; vector ops may fail", e)
        embeddings = None

    if embeddings is not None:
        max_retries = 3
        last_exc: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                test_vec = await asyncio.wait_for(
                    asyncio.to_thread(embeddings.embed_query, "connectivity check"),
                    timeout=30,
                )
                if not test_vec:
                    raise RuntimeError("Embedding service returned empty result")
                actual_dim = len(test_vec)
                expected_dim = settings.EMBEDDING_DIMENSION
                if actual_dim != expected_dim:
                    raise RuntimeError(
                        f"Embedding dimension mismatch: model returned {actual_dim}, "
                        f"EMBEDDING_DIMENSION={expected_dim}. Update EMBEDDING_DIMENSION "
                        f"in config to match the model output."
                    )
                logger.info("Embedding connectivity verified (dim=%d)", actual_dim)
                last_exc = None
                break
            except Exception as e:
                last_exc = e
                if attempt < max_retries:
                    logger.warning(
                        "Embedding connectivity check attempt %d/%d failed, retrying...",
                        attempt,
                        max_retries,
                    )
                    await asyncio.sleep(2 * attempt)
        if last_exc is not None:
            logger.warning(
                "Embedding connectivity check failed after %d attempts: %s. "
                "Vector operations may fail until the embedding service is available.",
                max_retries,
                last_exc,
            )

    # M29: Warn when a parser provider is configured but its API key is empty.
    # This catches misconfiguration early instead of failing silently at parse time.
    _parser_key_warnings: list[tuple[str, str]] = []
    _ocr_provider = (settings.OCR_PROVIDER or "").lower()
    if _ocr_provider.startswith("marker") and not settings.MARKER_API_KEY.get_secret_value():
        _parser_key_warnings.append(("marker", "MARKER_API_KEY"))
    if _ocr_provider.startswith("mineru") and not settings.MINERU_API_KEY.get_secret_value():
        _parser_key_warnings.append(("mineru", "MINERU_API_KEY"))
    if _ocr_provider.startswith("paddle") and not settings.PADDLEOCR_API_KEY.get_secret_value():
        _parser_key_warnings.append(("paddleocr", "PADDLEOCR_API_KEY"))
    for _provider, _key_name in _parser_key_warnings:
        logger.warning(
            "OCR_PROVIDER is '%s' but %s is empty. Cloud parsing will fail; "
            "the system will fall back to local text extraction. Set %s in "
            "your .env file or change OCR_PROVIDER.",
            _provider,
            _key_name,
            _key_name,
        )

    try:
        get_vectorstore()
        logger.info("Vector store singleton initialized")
    except Exception as e:
        logger.warning("Vector store initialization failed: %s; retrieval may fail", e)

    try:
        from ...services.knowledge_graph import get_entity_vectorstore
        from ...services.memory import get_memory_vectorstore, get_summary_vectorstore

        get_memory_vectorstore()
        get_summary_vectorstore()
        get_entity_vectorstore()
        logger.info("Memory/session/entity vectorstores pre-warmed")

        from ...services.memory._vectorstore import reconcile_orphan_memory_vectors

        orphan_count = await asyncio.to_thread(reconcile_orphan_memory_vectors)
        if orphan_count:
            logger.info("Cleaned up %d orphan memory vectors from prior crash", orphan_count)
    except Exception:
        record_best_effort_failure("vectorstore_pre_warm")
        logger.warning("Vectorstore pre-warm failed (non-fatal)", exc_info=True)

    try:
        from ...services.rag._summary_vectorstore import (
            get_summary_vectorstore as get_file_summary_vectorstore,
        )

        get_file_summary_vectorstore()
        logger.info("File summary vectorstore pre-warmed")
    except Exception:
        record_best_effort_failure("file_summary_vectorstore_pre_warm")
        logger.warning("Vectorstore pre-warm failed (non-fatal)", exc_info=True)

    if settings.RAGANYTHING_ENABLED:
        from ...services.rag._raganything import is_raganything_available

        if not is_raganything_available():
            raise RuntimeError(
                "RAGANYTHING_ENABLED=true but the 'raganything' package is not installed. "
                "Run: uv sync --extra multimodal"
            )
        from ...services.rag._raganything import _get_raganything

        try:
            await asyncio.to_thread(_get_raganything)
            logger.info("RAGAnything pre-warmed")
        except Exception as exc:
            record_best_effort_failure("raganything_pre_warm")
            logger.warning("RAGAnything pre-warm failed (continuing): %s", exc, exc_info=True)
