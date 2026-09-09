"""Graceful shutdown logic for the application lifespan."""

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
    from ...services.memory import _persist_session_cache, stop_memory_decay_loop
    from ...services.parser import close_parser_http_client
    from ...services.rag import persist_vectorstore
    from ...services.search import close_http_client
    from ...services.vision import close_vision_client

    _persist_session_cache()
    stop_memory_decay_loop()
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

    close_all_connections()

    logger.info("Shutdown complete")
