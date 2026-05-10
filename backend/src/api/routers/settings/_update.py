"""Settings PUT / reload endpoints and helpers."""

from typing import Any

from fastapi import HTTPException, Request
from pydantic import SecretStr, ValidationError

from ....api.middleware import limiter
from ....core.audit import audit_log
from ....core.config import settings
from ....core.settings_epoch import bump_settings_epoch
from ....models.schemas import SettingsResponse, SettingsUpdateRequest
from ....models.schemas._common import MessageResponse
from ._common import (
    _normalize_retriever_provider,
    _settings_lock,
    logger,
    rebuild_state,
    router,
    validate_settings_invariants,
)
from ._get import _get_current_settings


def _is_masked_value(value: str) -> bool:
    """Detect whether a submitted string is still masked (all * except last 4).

    A valid masked value must be longer than 4 chars and have all leading chars as '*'.
    Short strings (<=4 chars) cannot be masked since our mask format is '****xxxx'.
    """
    if not value or len(value) <= 4:
        return False
    masked_part = value[:-4]
    return all(ch == "*" for ch in masked_part)


def _rebuild_required(req: SettingsUpdateRequest) -> tuple[bool, str]:
    """Return (needs_rebuild, reason) when setting changes invalidate vectors."""
    reasons: list[str] = []

    if req.embedding:
        if req.embedding.binding != settings.EMBEDDING_BINDING:
            reasons.append("embedding binding")
        if req.embedding.model != settings.EMBEDDING_MODEL:
            reasons.append("embedding model")
        if req.embedding.dimension != settings.EMBEDDING_DIMENSION:
            reasons.append("embedding dimension")

    if req.rag:
        checks: dict[str, tuple[Any, Any]] = {
            "chunk_size": (req.rag.chunk_size, settings.CHUNK_SIZE),
            "chunk_overlap": (req.rag.chunk_overlap, settings.CHUNK_OVERLAP),
            "speaker_in_content": (req.rag.speaker_in_content, settings.AUDIO_SPEAKER_IN_CONTENT),
            "audio_semantic_boundary_enabled": (
                req.rag.audio_semantic_boundary_enabled,
                settings.AUDIO_SEMANTIC_BOUNDARY_ENABLED,
            ),
            "audio_semantic_boundary_threshold": (
                req.rag.audio_semantic_boundary_threshold,
                settings.AUDIO_SEMANTIC_BOUNDARY_THRESHOLD,
            ),
            "audio_semantic_min_segments": (
                req.rag.audio_semantic_min_segments,
                settings.AUDIO_SEMANTIC_MIN_SEGMENTS,
            ),
            "audio_semantic_max_segments": (
                req.rag.audio_semantic_max_segments,
                settings.AUDIO_SEMANTIC_MAX_SEGMENTS,
            ),
            "split_on_speaker_change": (
                req.rag.split_on_speaker_change,
                settings.AUDIO_SPLIT_ON_SPEAKER_CHANGE,
            ),
            "non_text_chunking_strategy": (
                req.rag.non_text_chunking_strategy,
                settings.NON_TEXT_CHUNKING_STRATEGY,
            ),
        }
        for name, (new_val, old_val) in checks.items():
            if new_val is not None and new_val != old_val:
                reasons.append(name)

    return (bool(reasons), "; ".join(reasons))


def _update_settings_in_memory(req: SettingsUpdateRequest) -> None:
    """Update settings in memory (does not persist to file)."""
    if req.llm:
        settings.LLM_BINDING = req.llm.binding
        settings.LLM_MODEL = req.llm.model
        settings.LLM_TEMPERATURE = req.llm.temperature
        settings.LLM_MAX_TOKENS = req.llm.max_tokens
        if req.llm.base_url is not None:
            settings.LLM_BASE_URL = req.llm.base_url
        if req.llm.host is not None:
            settings.LLM_HOST = req.llm.host
        if req.llm.api_key and not _is_masked_value(req.llm.api_key):
            settings.LLM_API_KEY = SecretStr(req.llm.api_key)

    if req.embedding:
        settings.EMBEDDING_BINDING = req.embedding.binding
        settings.EMBEDDING_MODEL = req.embedding.model
        settings.EMBEDDING_DIMENSION = req.embedding.dimension
        if req.embedding.base_url is not None:
            settings.EMBEDDING_BASE_URL = req.embedding.base_url
        if req.embedding.host is not None:
            settings.EMBEDDING_HOST = req.embedding.host
        if req.embedding.api_key and not _is_masked_value(req.embedding.api_key):
            settings.EMBEDDING_API_KEY = SecretStr(req.embedding.api_key)

    if req.rag:
        settings.CHUNK_SIZE = req.rag.chunk_size
        settings.CHUNK_OVERLAP = req.rag.chunk_overlap
        settings.TOP_K = req.rag.top_k
        settings.QUERY_REWRITE_ENABLED = req.rag.query_rewrite_enabled
        if req.rag.query_rewrite_model is not None:
            settings.QUERY_REWRITE_MODEL = req.rag.query_rewrite_model
        settings.SCORE_THRESHOLD = req.rag.score_threshold
        if req.rag.distance_metric is not None:
            settings.DISTANCE_METRIC = req.rag.distance_metric
        settings.RERANKER_BINDING = req.rag.reranker_binding
        settings.RERANKER_MODEL = req.rag.reranker_model
        if req.rag.reranker_base_url is not None:
            settings.RERANKER_BASE_URL = req.rag.reranker_base_url
        if req.rag.reranker_api_key and not _is_masked_value(req.rag.reranker_api_key):
            settings.RERANKER_API_KEY = SecretStr(req.rag.reranker_api_key)
        settings.RERANKER_TOP_N = req.rag.reranker_top_n
        if req.rag.reranker_min_score is not None:
            settings.RERANKER_MIN_SCORE = req.rag.reranker_min_score
        if req.rag.reranker_timeout_seconds is not None:
            settings.RERANKER_TIMEOUT_SECONDS = req.rag.reranker_timeout_seconds
        if req.rag.fetch_multiplier is not None:
            settings.RAG_RERANK_FETCH_MULTIPLIER = req.rag.fetch_multiplier
        if req.rag.persist_interval_seconds is not None:
            settings.RAG_PERSIST_INTERVAL_SECONDS = req.rag.persist_interval_seconds
        settings.PARENT_CHILD_ENABLED = req.rag.parent_child_enabled
        settings.CHILD_CHUNK_SIZE = req.rag.child_chunk_size
        settings.CHILD_CHUNK_OVERLAP = req.rag.child_chunk_overlap
        settings.HYBRID_SEARCH_ENABLED = req.rag.hybrid_search_enabled
        settings.HYBRID_ALPHA = req.rag.hybrid_alpha
        settings.RAG_RETRIEVER_PROVIDER = _normalize_retriever_provider(req.rag.retriever_provider)
        settings.RAGANYTHING_ENABLED = req.rag.raganything_enabled
        settings.RAGANYTHING_FALLBACK_TO_NATIVE = req.rag.raganything_fallback_to_native
        if req.rag.raganything_working_dir is not None:
            settings.RAGANYTHING_WORKING_DIR = req.rag.raganything_working_dir
        if req.rag.raganything_index_timeout_seconds is not None:
            settings.RAGANYTHING_INDEX_TIMEOUT_SECONDS = req.rag.raganything_index_timeout_seconds
        if req.rag.raganything_query_timeout_seconds is not None:
            settings.RAGANYTHING_QUERY_TIMEOUT_SECONDS = req.rag.raganything_query_timeout_seconds
        if req.rag.raganything_llm_timeout_seconds is not None:
            settings.RAGANYTHING_LLM_TIMEOUT_SECONDS = req.rag.raganything_llm_timeout_seconds
        settings.SEMANTIC_CHUNKING_ENABLED = req.rag.semantic_chunking_enabled
        if req.rag.non_text_chunking_strategy is not None:
            settings.NON_TEXT_CHUNKING_STRATEGY = req.rag.non_text_chunking_strategy
        settings.MULTI_QUERY_ENABLED = req.rag.multi_query_enabled
        if req.rag.multi_query_count is not None:
            settings.MULTI_QUERY_COUNT = req.rag.multi_query_count
        if req.rag.index_tables is not None:
            settings.RAG_INDEX_TABLES = req.rag.index_tables
        if req.rag.index_image_captions is not None:
            settings.RAG_INDEX_IMAGE_CAPTIONS = req.rag.index_image_captions
        if req.rag.image_ocr_min_length is not None:
            settings.RAG_IMAGE_OCR_MIN_LENGTH = req.rag.image_ocr_min_length
        if req.rag.content_type_rerank_enabled is not None:
            settings.RAG_CONTENT_TYPE_RERANK_ENABLED = req.rag.content_type_rerank_enabled
        if req.rag.sibling_coretrieve_enabled is not None:
            settings.RAG_SIBLING_CORETRIEVE_ENABLED = req.rag.sibling_coretrieve_enabled
        if req.rag.sibling_coretrieve_per_anchor is not None:
            settings.RAG_SIBLING_CORETRIEVE_PER_ANCHOR = req.rag.sibling_coretrieve_per_anchor
        if req.rag.sibling_coretrieve_max_total is not None:
            settings.RAG_SIBLING_CORETRIEVE_MAX_TOTAL = req.rag.sibling_coretrieve_max_total
        settings.AUDIO_SEMANTIC_BOUNDARY_ENABLED = req.rag.audio_semantic_boundary_enabled
        if req.rag.audio_semantic_boundary_threshold is not None:
            settings.AUDIO_SEMANTIC_BOUNDARY_THRESHOLD = req.rag.audio_semantic_boundary_threshold
        if req.rag.audio_semantic_min_segments is not None:
            settings.AUDIO_SEMANTIC_MIN_SEGMENTS = req.rag.audio_semantic_min_segments
        if req.rag.audio_semantic_max_segments is not None:
            settings.AUDIO_SEMANTIC_MAX_SEGMENTS = req.rag.audio_semantic_max_segments
        if req.rag.speaker_in_content is not None:
            settings.AUDIO_SPEAKER_IN_CONTENT = req.rag.speaker_in_content
        if req.rag.split_on_speaker_change is not None:
            settings.AUDIO_SPLIT_ON_SPEAKER_CHANGE = req.rag.split_on_speaker_change

    if req.memory:
        settings.MEMORY_AUTO_EXTRACT = req.memory.auto_extract
        settings.MEMORY_MAX_FACTS_PER_TURN = req.memory.max_facts_per_turn
        settings.MEMORY_DECAY_ENABLED = req.memory.decay_enabled
        if req.memory.decay_interval_hours is not None:
            settings.MEMORY_DECAY_INTERVAL_HOURS = req.memory.decay_interval_hours
        settings.MEMORY_TTL_DAYS = req.memory.ttl_days
        settings.SESSION_MAX_HISTORY = req.memory.session_max_history
        if req.memory.max_context_items is not None:
            settings.MEMORY_MAX_CONTEXT_ITEMS = req.memory.max_context_items
        if req.memory.session_max_tokens is not None:
            settings.SESSION_MAX_TOKENS = req.memory.session_max_tokens
        if req.memory.session_summary_enabled is not None:
            settings.SESSION_SUMMARY_ENABLED = req.memory.session_summary_enabled
        if req.memory.session_summary_min_turns is not None:
            settings.SESSION_SUMMARY_MIN_TURNS = req.memory.session_summary_min_turns
        if req.memory.session_summary_max_items is not None:
            settings.SESSION_SUMMARY_MAX_ITEMS = req.memory.session_summary_max_items
        if req.memory.session_summary_max_messages is not None:
            settings.SESSION_SUMMARY_MAX_MESSAGES = req.memory.session_summary_max_messages
        if req.memory.session_summary_idle_minutes is not None:
            settings.SESSION_SUMMARY_IDLE_MINUTES = req.memory.session_summary_idle_minutes
        if req.memory.session_summary_startup_backfill is not None:
            settings.SESSION_SUMMARY_STARTUP_BACKFILL = req.memory.session_summary_startup_backfill
        if req.memory.consolidation_enabled is not None:
            settings.MEMORY_CONSOLIDATION_ENABLED = req.memory.consolidation_enabled
        if req.memory.consolidation_min_cluster is not None:
            settings.MEMORY_CONSOLIDATION_MIN_CLUSTER = req.memory.consolidation_min_cluster
        if req.memory.semantic_cluster_enabled is not None:
            settings.MEMORY_SEMANTIC_CLUSTER_ENABLED = req.memory.semantic_cluster_enabled
        if req.memory.knowledge_graph_enabled is not None:
            settings.KNOWLEDGE_GRAPH_ENABLED = req.memory.knowledge_graph_enabled
        if req.memory.profile_enabled is not None:
            settings.MEMORY_PROFILE_ENABLED = req.memory.profile_enabled
        if req.memory.profile_refresh_interval is not None:
            settings.MEMORY_PROFILE_REFRESH_INTERVAL = req.memory.profile_refresh_interval
        if req.memory.extraction_mode is not None:
            settings.MEMORY_EXTRACTION_MODE = req.memory.extraction_mode
        if req.memory.entity_relations_limit is not None:
            settings.ENTITY_RELATIONS_LIMIT = req.memory.entity_relations_limit
        if req.memory.global_memory_limit is not None:
            settings.GLOBAL_MEMORY_LIMIT = req.memory.global_memory_limit
        if req.memory.skip_threshold is not None:
            settings.SESSION_CONTEXT_SKIP_THRESHOLD = req.memory.skip_threshold

    if req.search:
        settings.SEARCH_BINDING = req.search.binding
        settings.SEARCH_REGION = req.search.region
        settings.SEARCH_MAX_RESULTS = req.search.max_results
        settings.SEARCH_TIMEOUT = req.search.timeout
        if req.search.web_search_timeout_s is not None:
            settings.WEB_SEARCH_TIMEOUT_S = req.search.web_search_timeout_s
        if req.search.api_key and not _is_masked_value(req.search.api_key):
            settings.SEARCH_API_KEY = SecretStr(req.search.api_key)

    if req.upload:
        settings.MAX_UPLOAD_SIZE_MB = req.upload.max_size_mb
        if req.upload.auto_summarize_files is not None:
            settings.MEETING_AUTO_SUMMARIZE_FILES = req.upload.auto_summarize_files
        if req.upload.multimodal_captioning_enabled is not None:
            settings.MULTIMODAL_CAPTIONING_ENABLED = req.upload.multimodal_captioning_enabled
        if req.upload.ocr_dedup_enabled is not None:
            settings.MULTIMODAL_CAPTION_OCR_DEDUP_ENABLED = req.upload.ocr_dedup_enabled
        if req.upload.ocr_dedup_timeout_seconds is not None:
            settings.MULTIMODAL_CAPTION_OCR_DEDUP_TIMEOUT_SECONDS = (
                req.upload.ocr_dedup_timeout_seconds
            )
        if req.upload.video_keyframes_enabled is not None:
            settings.VIDEO_KEYFRAMES_ENABLED = req.upload.video_keyframes_enabled

    if req.asr:
        if req.asr.provider is not None:
            settings.ASR_PROVIDER = req.asr.provider  # type: ignore[assignment]
        if req.asr.language is not None:
            settings.ASR_LANGUAGE = req.asr.language
        if req.asr.assemblyai_api_key and not _is_masked_value(req.asr.assemblyai_api_key):
            settings.ASSEMBLYAI_API_KEY = SecretStr(req.asr.assemblyai_api_key)
        if req.asr.speech_model is not None:
            settings.ASSEMBLYAI_SPEECH_MODEL = req.asr.speech_model
        if req.asr.speaker_labels is not None:
            settings.ASSEMBLYAI_SPEAKER_LABELS = req.asr.speaker_labels
        if req.asr.language_detection is not None:
            settings.ASSEMBLYAI_LANGUAGE_DETECTION = req.asr.language_detection
        if req.asr.poll_interval_seconds is not None:
            settings.ASSEMBLYAI_POLL_INTERVAL_SECONDS = req.asr.poll_interval_seconds
        if req.asr.max_wait_seconds is not None:
            settings.ASSEMBLYAI_MAX_WAIT_SECONDS = req.asr.max_wait_seconds

    if req.ocr:
        if req.ocr.provider is not None:
            settings.OCR_PROVIDER = req.ocr.provider
        if req.ocr.language is not None:
            settings.OCR_LANGUAGE = req.ocr.language
        if req.ocr.dpi is not None:
            settings.OCR_DPI = req.ocr.dpi
        if req.ocr.marker_base_url is not None:
            settings.MARKER_BASE_URL = req.ocr.marker_base_url
        if req.ocr.marker_api_key and not _is_masked_value(req.ocr.marker_api_key):
            settings.MARKER_API_KEY = SecretStr(req.ocr.marker_api_key)
        if req.ocr.marker_max_wait_seconds is not None:
            settings.MARKER_MAX_WAIT_SECONDS = req.ocr.marker_max_wait_seconds
        if req.ocr.mineru_base_url is not None:
            settings.MINERU_BASE_URL = req.ocr.mineru_base_url
        if req.ocr.mineru_api_key and not _is_masked_value(req.ocr.mineru_api_key):
            settings.MINERU_API_KEY = SecretStr(req.ocr.mineru_api_key)
        if req.ocr.mineru_max_wait_seconds is not None:
            settings.MINERU_MAX_WAIT_SECONDS = req.ocr.mineru_max_wait_seconds
        if req.ocr.paddleocr_base_url is not None:
            settings.PADDLEOCR_BASE_URL = req.ocr.paddleocr_base_url
        if req.ocr.paddleocr_api_key and not _is_masked_value(req.ocr.paddleocr_api_key):
            settings.PADDLEOCR_API_KEY = SecretStr(req.ocr.paddleocr_api_key)
        if req.ocr.http_timeout_seconds is not None:
            settings.PARSER_HTTP_TIMEOUT_SECONDS = req.ocr.http_timeout_seconds
        if req.ocr.poll_interval_seconds is not None:
            settings.PARSER_POLL_INTERVAL_SECONDS = req.ocr.poll_interval_seconds

    if req.vision:
        if req.vision.model is not None:
            settings.VISION_MODEL = req.vision.model
        if req.vision.api_key and not _is_masked_value(req.vision.api_key):
            settings.VISION_API_KEY = SecretStr(req.vision.api_key)
        if req.vision.base_url is not None:
            settings.VISION_BASE_URL = req.vision.base_url
        if req.vision.retry_max_attempts is not None:
            settings.VISION_RETRY_MAX_ATTEMPTS = req.vision.retry_max_attempts
        if req.vision.retry_base_delay_seconds is not None:
            settings.VISION_RETRY_BASE_DELAY_SECONDS = req.vision.retry_base_delay_seconds
        if req.vision.retry_max_delay_seconds is not None:
            settings.VISION_RETRY_MAX_DELAY_SECONDS = req.vision.retry_max_delay_seconds
        if req.vision.caption_min_chars is not None:
            settings.VISION_CAPTION_MIN_CHARS = req.vision.caption_min_chars
        if req.vision.ocr_min_chars is not None:
            settings.VISION_OCR_MIN_CHARS = req.vision.ocr_min_chars

    if req.tts:
        if req.tts.binding is not None:
            settings.TTS_BINDING = req.tts.binding
        if req.tts.model is not None:
            settings.TTS_MODEL = req.tts.model
        if req.tts.api_key and not _is_masked_value(req.tts.api_key):
            settings.TTS_API_KEY = SecretStr(req.tts.api_key)
        if req.tts.base_url is not None:
            settings.TTS_BASE_URL = req.tts.base_url
        if req.tts.voice is not None:
            settings.TTS_VOICE = req.tts.voice
        if req.tts.speed is not None:
            settings.TTS_SPEED = req.tts.speed

    if req.parser:
        if req.parser.max_parse_pages is not None:
            settings.MAX_PARSE_PAGES = req.parser.max_parse_pages
        if req.parser.parse_timeout_seconds is not None:
            settings.PARSE_TIMEOUT_SECONDS = req.parser.parse_timeout_seconds
        if req.parser.timeout_per_mb_seconds is not None:
            settings.PARSE_TIMEOUT_PER_MB_SECONDS = req.parser.timeout_per_mb_seconds
        if req.parser.timeout_max_seconds is not None:
            settings.PARSE_TIMEOUT_MAX_SECONDS = req.parser.timeout_max_seconds
        if req.parser.max_images_per_page is not None:
            settings.PARSER_MAX_IMAGES_PER_PAGE = req.parser.max_images_per_page
        if req.parser.max_image_bytes is not None:
            settings.PARSER_MAX_IMAGE_BYTES = req.parser.max_image_bytes
        if req.parser.doc_clean_repetition_min_pages is not None:
            settings.DOC_CLEAN_REPETITION_MIN_PAGES = req.parser.doc_clean_repetition_min_pages
        if req.parser.doc_clean_repetition_min_ratio is not None:
            settings.DOC_CLEAN_REPETITION_MIN_RATIO = req.parser.doc_clean_repetition_min_ratio
        if req.parser.doc_clean_header_footer_max_lines is not None:
            settings.DOC_CLEAN_HEADER_FOOTER_MAX_LINES = (
                req.parser.doc_clean_header_footer_max_lines
            )
        if req.parser.doc_clean_repetition_max_line_length is not None:
            settings.DOC_CLEAN_REPETITION_MAX_LINE_LENGTH = (
                req.parser.doc_clean_repetition_max_line_length
            )

    if req.retention:
        if req.retention.chat_message_retention_days is not None:
            settings.CHAT_MESSAGE_RETENTION_DAYS = req.retention.chat_message_retention_days
        if req.retention.decay_state_retention_days is not None:
            settings.DECAY_STATE_RETENTION_DAYS = req.retention.decay_state_retention_days

    # Singleton reset contract (C-H1): when provider-critical settings change,
    # the corresponding singleton MUST be reset so the next call picks up the
    # new binding / model / API key.
    if req.llm:
        from ....services.llm import reset_llm

        reset_llm()
    if req.embedding:
        from ....services.embedder import reset_embeddings

        reset_embeddings()
    if req.rag and (
        req.rag.reranker_binding
        or req.rag.reranker_model
        or req.rag.reranker_base_url
        or req.rag.reranker_api_key
    ):
        from ....services.rag._reranker import reset_reranker_state

        reset_reranker_state()
    if req.rag and req.rag.query_rewrite_model is not None:
        from ....services.rag._query import reset_rewrite_llm

        reset_rewrite_llm()


@router.put("", response_model=SettingsResponse)
@limiter.limit("5/minute")
async def update_settings(
    request: Request,
    settings_request: SettingsUpdateRequest,
) -> SettingsResponse:
    """Update application configuration settings (in-memory only)."""
    try:
        with _settings_lock:
            if rebuild_state.vectors or rebuild_state.multimodal:
                raise HTTPException(
                    status_code=409,
                    detail="Cannot update settings while rebuild jobs are running",
                )
            needs_rebuild, reason = _rebuild_required(settings_request)
            if needs_rebuild and not settings_request.confirm_vector_rebuild:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Settings changed require vector rebuild: {reason}. "
                        "Confirm with confirm_vector_rebuild=true and run "
                        "/settings/rebuild-vectors."
                    ),
                )
            validate_settings_invariants(
                top_k=settings_request.rag.top_k if settings_request.rag else None,
                reranker_top_n=(
                    settings_request.rag.reranker_top_n if settings_request.rag else None
                ),
                hybrid_alpha=(settings_request.rag.hybrid_alpha if settings_request.rag else None),
                rag_retriever_provider=(
                    settings_request.rag.retriever_provider if settings_request.rag else None
                ),
            )
            _update_settings_in_memory(settings_request)
            from ....services.registry import (
                initialize_default_resettable_services,
                reset_all_services,
            )

            initialize_default_resettable_services()
            reset_all_services()
            # Bump epoch LAST so readers never see a new epoch with stale config.
            epoch = bump_settings_epoch()
        audit_log("update", "settings", f"runtime@epoch={epoch}")
        logger.info("Settings updated successfully")
        return _get_current_settings()
    except HTTPException:
        raise
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors()) from e
    except Exception as e:
        logger.error("Failed to update settings: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update settings") from e


@router.post("/reload-config", response_model=MessageResponse)
async def reload_config() -> dict[str, str]:
    """Reload non-secret settings from config/main.yaml (auth required)."""
    from ....core.config import reload_settings

    try:
        with _settings_lock:
            if rebuild_state.vectors or rebuild_state.multimodal:
                raise HTTPException(
                    status_code=409,
                    detail="Cannot reload settings while rebuild jobs are running",
                )
            reload_settings()
            from ....services.registry import (
                initialize_default_resettable_services,
                reset_all_services,
            )

            initialize_default_resettable_services()
            reset_all_services()
            # Bump epoch LAST so readers never see a new epoch with stale config.
            epoch = bump_settings_epoch()
        audit_log("reload", "settings", f"runtime@epoch={epoch}")
        logger.info("Settings reloaded from %s", reload_settings.__module__)
        return {"message": "Settings reloaded successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to reload settings: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to reload settings") from e
