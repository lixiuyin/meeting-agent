"""Graceful shutdown logic for the application lifespan."""

import asyncio
import logging

from ...core.database import close_all_connections

logger = logging.getLogger(__name__)


async def graceful_shutdown() -> None:
    """Cancel background tasks, persist state, and close resources."""
    from ...utils.supervised_task import get_background_tasks

    _bg = get_background_tasks()

    logger.info("Shutting down, cancelling %d background tasks", _bg.active_count)

    cancelled = _bg.cancel_all()
    if cancelled:
        logger.info("Cancelled %d background tasks", len(cancelled))
        await _bg.wait_all(timeout=5.0)

    from ...services.asr._assemblyai import close_http_client as close_asr_http_client
    from ...services.chain import cancel_background_tasks
    from ...services.memory import _persist_session_cache, stop_memory_decay_loop
    from ...services.parser import close_parser_http_client
    from ...services.rag import persist_vectorstore
    from ...services.search import close_http_client
    from ...services.vision import close_vision_client

    _persist_session_cache()
    stop_memory_decay_loop()
    cancel_background_tasks()
    persist_vectorstore()
    await close_http_client()
    await close_asr_http_client()
    await close_vision_client()

    try:
        from ...services.rag._reranker import _close_reranker_http_client

        await _close_reranker_http_client()
    except Exception:
        logger.warning("Reranker HTTP client cleanup failed", exc_info=True)

    try:
        await close_parser_http_client()
    except Exception:
        logger.warning("Parser httpx client cleanup failed", exc_info=True)

    background_tasks: list[asyncio.Task] = [
        t for t in asyncio.all_tasks() if t is not asyncio.current_task()
    ]
    if background_tasks:
        for t in background_tasks:
            t.cancel()
        done, pending = await asyncio.wait(background_tasks, timeout=5)
        for t in pending:
            logger.warning("Background task %s did not finish within timeout", t.get_name())
        for t in done:
            if t.cancelled():
                continue
            exc = t.exception()
            if exc:
                logger.error("Background task %s raised: %s", t.get_name(), exc)

    close_all_connections()

    from ...services.parser.cascade import _PARSER_LOOP_EXECUTOR
    from ...services.rag._retriever import _VECTOR_SEARCH_EXECUTOR

    _PARSER_LOOP_EXECUTOR.shutdown(wait=False, cancel_futures=True)
    _VECTOR_SEARCH_EXECUTOR.shutdown(wait=False, cancel_futures=True)

    logger.info("Shutdown complete")
