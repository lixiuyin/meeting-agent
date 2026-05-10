"""Application configuration - loads defaults from YAML, overrides from env"""

from pathlib import Path
from typing import ClassVar, Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ._config_snapshot import SettingsSnapshot, build_settings_snapshot  # noqa: F401
from ._config_validation import (
    _MAX_WIDE_FETCH_SIZE,
    _VALID_ANCHOR_TTL_MODES,
    _VALID_FILE_PRIOR_MODES,
    _VALID_FILE_SCOPING_MODES,
    _VALID_FUNNEL_AGGREGATIONS,
    _VALID_FUNNEL_EVIDENCE_MODES,
    _VALID_FUNNEL_MERGE_STRATEGIES,
)
from ._config_yaml import _yaml_get, reload_yaml_config
from .constants import DB_PATH, PROJECT_ROOT, UPLOAD_DIR, VECTOR_DB_DIR


class Settings(BaseSettings):
    _VALID_FILE_SCOPING_MODES: ClassVar[frozenset[str]] = _VALID_FILE_SCOPING_MODES
    _VALID_FUNNEL_MERGE_STRATEGIES: ClassVar[frozenset[str]] = _VALID_FUNNEL_MERGE_STRATEGIES
    _VALID_FUNNEL_AGGREGATIONS: ClassVar[frozenset[str]] = _VALID_FUNNEL_AGGREGATIONS
    _VALID_FUNNEL_EVIDENCE_MODES: ClassVar[frozenset[str]] = _VALID_FUNNEL_EVIDENCE_MODES
    _VALID_ANCHOR_TTL_MODES: ClassVar[frozenset[str]] = _VALID_ANCHOR_TTL_MODES
    _VALID_FILE_PRIOR_MODES: ClassVar[frozenset[str]] = _VALID_FILE_PRIOR_MODES
    _MAX_WIDE_FETCH_SIZE: ClassVar[int] = _MAX_WIDE_FETCH_SIZE

    # Project paths (from constants)
    BASE_DIR: Path = PROJECT_ROOT
    UPLOAD_DIR: Path = UPLOAD_DIR
    VECTOR_DB_DIR: Path = VECTOR_DB_DIR
    DB_PATH: Path = DB_PATH

    # LLM (supports multiple providers: openai, anthropic, deepseek, openrouter, groq, together,
    # mistral, ollama, lm_studio, vllm, llama_cpp, azure_openai)
    LLM_BINDING: str = _yaml_get("llm", "binding", default="openai")
    LLM_MODEL: str = _yaml_get("llm", "model", default="gpt-4o-mini")
    LLM_API_KEY: SecretStr = SecretStr("")
    LLM_BASE_URL: str = ""
    LLM_HOST: str = ""  # For local providers (ollama, lm_studio, vllm)
    LLM_TEMPERATURE: float = Field(
        default=_yaml_get("llm", "temperature", default=0.3), ge=0.0, le=2.0
    )
    LLM_MAX_TOKENS: int = _yaml_get("llm", "max_tokens", default=2048)
    LLM_CONTEXT_WINDOW: int = _yaml_get("llm", "context_window", default=128_000)
    # LLM Response Cache
    LLM_CACHE_ENABLED: bool = _yaml_get("llm", "cache_enabled", default=True)
    LLM_CACHE_TTL_SECONDS: int = _yaml_get("llm", "cache_ttl_seconds", default=300)
    LLM_CACHE_MAX_SIZE: int = _yaml_get("llm", "cache_max_size", default=512)
    # LLM Traffic Control
    LLM_MAX_CONCURRENCY: int = _yaml_get("llm", "max_concurrency", default=10)
    LLM_RPM: int = _yaml_get("llm", "rpm", default=60)
    LLM_CIRCUIT_BREAKER_THRESHOLD: int = _yaml_get("llm", "circuit_breaker_threshold", default=5)
    LLM_CIRCUIT_BREAKER_RECOVERY: int = _yaml_get("llm", "circuit_breaker_recovery", default=60)
    LLM_RETRY_MAX_ATTEMPTS: int = _yaml_get("llm", "retry_max_attempts", default=3)
    LLM_PROMPT_RESERVE_TOKENS: int = _yaml_get("llm", "prompt_reserve_tokens", default=500)
    LLM_HISTORY_BUDGET_CHARS: int = _yaml_get("llm", "history_budget_chars", default=16_000)
    PROMPT_TOTAL_BUDGET_TOKENS: int = _yaml_get("llm", "prompt_total_budget_tokens", default=6000)
    ANTHROPIC_PROMPT_CACHE_ENABLED: bool = _yaml_get(
        "llm", "anthropic_prompt_cache_enabled", default=True
    )
    ANTHROPIC_PROMPT_CACHE_MIN_CHARS: int = _yaml_get(
        "llm", "anthropic_prompt_cache_min_chars", default=1024
    )
    EMBEDDING_QUERY_CACHE_ENABLED: bool = _yaml_get(
        "embedding", "query_cache_enabled", default=True
    )
    EMBEDDING_QUERY_CACHE_SIZE: int = _yaml_get("embedding", "query_cache_size", default=64)
    # H-8: Stampede protection wait timeout (seconds). 0 = use provider-aware default.
    EMBEDDING_STAMPEDE_WAIT_S: float = _yaml_get("embedding", "stampede_wait_s", default=0)
    # HMAC pepper for user-id derivation (N-M-1). Falls back to hardcoded default in dev.
    PRINCIPAL_PEPPER: SecretStr = SecretStr("")
    VECTOR_SEARCH_TIMEOUT_S: float = _yaml_get("rag", "vector_search_timeout_s", default=8.0)
    MULTIMODAL_ATTACH_GATE_ENABLED: bool = _yaml_get(
        "rag", "multimodal_attach_gate_enabled", default=True
    )
    # Vision support: "auto" (detect from model name), "true", or "false"
    LLM_SUPPORTS_VISION: str = _yaml_get("llm", "supports_vision", default="auto")

    # Embedding provider: openai, azure_openai, jina, cohere, huggingface, google, ollama, lm_studio
    EMBEDDING_BINDING: str = _yaml_get("embedding", "binding", default="openai")
    EMBEDDING_MODEL: str = _yaml_get("embedding", "model", default="text-embedding-3-small")
    EMBEDDING_API_KEY: SecretStr = SecretStr("")
    EMBEDDING_BASE_URL: str = ""
    EMBEDDING_HOST: str = ""  # For Ollama/LM Studio local endpoints
    EMBEDDING_DIMENSION: int = _yaml_get("embedding", "dimension", default=1536)

    # Vector deletion dead-letter threshold
    VECTOR_DELETION_MAX_ATTEMPTS: int = Field(
        default=_yaml_get("vector", "deletion_max_attempts", default=10), ge=1, le=100
    )

    # ASR (Automatic Speech Recognition)
    # Only AssemblyAI is supported; the provider field is kept for forward
    # compatibility and for existing call sites that pass `provider=settings.ASR_PROVIDER`.
    ASR_PROVIDER: Literal["assemblyai"] = "assemblyai"  # type: ignore[assignment]

    @model_validator(mode="after")
    def _validate_asr_provider(self) -> "Settings":
        if self.ASR_PROVIDER.lower() != "assemblyai":
            raise ValueError(
                f"Only ASR_PROVIDER='assemblyai' is supported, got '{self.ASR_PROVIDER}'"
            )
        return self

    @model_validator(mode="after")
    def _validate_rag_retriever_provider(self) -> "Settings":
        """Validate RAG retriever provider requires available extras.

        When ``RAGANYTHING_ENABLED`` is True and the retriever provider is
        ``raganything``, verify the ``multimodal`` optional extra is installed
        so the error surfaces at startup instead of at first request time.
        """
        if self.RAGANYTHING_ENABLED and self.RAG_RETRIEVER_PROVIDER.lower() == "raganything":
            try:
                from raganything import RAGAnything  # noqa: F401
            except ImportError:
                raise RuntimeError(
                    "RAG_RETRIEVER_PROVIDER='raganything' requires the "
                    "'multimodal' optional extra.  Run: uv sync --extra multimodal"
                ) from None
        return self

    ASR_LANGUAGE: str = _yaml_get("asr", "language", default="en")
    # AssemblyAI (cloud ASR with speaker diarization) — API key is env-only
    ASSEMBLYAI_API_KEY: SecretStr = SecretStr("")
    ASSEMBLYAI_SPEECH_MODEL: str = _yaml_get(
        "asr", "assemblyai_speech_model", default="universal-3-pro"
    )
    ASSEMBLYAI_SPEAKER_LABELS: bool = _yaml_get("asr", "assemblyai_speaker_labels", default=True)
    ASSEMBLYAI_LANGUAGE_DETECTION: bool = _yaml_get(
        "asr", "assemblyai_language_detection", default=True
    )
    ASSEMBLYAI_POLL_INTERVAL_SECONDS: int = _yaml_get(
        "asr", "assemblyai_poll_interval_seconds", default=3
    )
    ASSEMBLYAI_MAX_WAIT_SECONDS: int = _yaml_get("asr", "assemblyai_max_wait_seconds", default=1800)

    # OCR parser cascade: marker → mineru → paddleocr
    OCR_PROVIDER: str = _yaml_get("ocr", "provider", default="marker")
    OCR_LANGUAGE: str = _yaml_get("ocr", "language", default="en")
    OCR_DPI: int = _yaml_get("ocr", "dpi", default=300)

    # Cloud parser API credentials (HTTP-based Marker / MinerU / PaddleOCR)
    MARKER_BASE_URL: str = _yaml_get(
        "ocr", "marker_base_url", default="https://www.datalab.to/api/v1/marker"
    )
    MARKER_API_KEY: SecretStr = SecretStr(_yaml_get("ocr", "marker_api_key", default=""))
    MARKER_MAX_WAIT_SECONDS: int = _yaml_get("ocr", "marker_max_wait_seconds", default=300)

    # API root for the v4 batch flow (file-urls/batch + extract-results/batch).
    # Legacy values like ``.../api/v4/extract/task`` are accepted — the parser
    # strips the suffix at runtime — but new configs should set the root.
    MINERU_BASE_URL: str = _yaml_get("ocr", "mineru_base_url", default="https://mineru.net/api/v4")
    MINERU_API_KEY: SecretStr = SecretStr(_yaml_get("ocr", "mineru_api_key", default=""))
    MINERU_MAX_WAIT_SECONDS: int = _yaml_get("ocr", "mineru_max_wait_seconds", default=600)

    PADDLEOCR_BASE_URL: str = _yaml_get("ocr", "paddleocr_base_url", default="")
    PADDLEOCR_API_KEY: SecretStr = SecretStr(_yaml_get("ocr", "paddleocr_api_key", default=""))

    PARSER_HTTP_TIMEOUT_SECONDS: float = _yaml_get(
        "ocr", "parser_http_timeout_seconds", default=180.0
    )
    PARSER_POLL_INTERVAL_SECONDS: float = _yaml_get(
        "ocr", "parser_poll_interval_seconds", default=2.0
    )

    # TTS (Text-to-Speech)
    TTS_BINDING: str = _yaml_get("tts", "binding") or ""
    TTS_MODEL: str = _yaml_get("tts", "model") or ""
    TTS_API_KEY: SecretStr = SecretStr("")
    TTS_BASE_URL: str = ""
    TTS_VOICE: str = _yaml_get("tts", "voice") or ""
    TTS_SPEED: float = _yaml_get("tts", "speed") or 1.0

    # Vision model (OpenAI-compatible multimodal endpoint)
    VISION_MODEL: str = _yaml_get("vision", "model", default="")
    VISION_API_KEY: SecretStr = SecretStr(_yaml_get("vision", "api_key", default=""))
    VISION_BASE_URL: str = _yaml_get("vision", "base_url", default="")
    VISION_RETRY_MAX_ATTEMPTS: int = _yaml_get("vision", "retry_max_attempts", default=3)
    VISION_RETRY_BASE_DELAY_SECONDS: float = _yaml_get(
        "vision", "retry_base_delay_seconds", default=0.5
    )
    VISION_RETRY_MAX_DELAY_SECONDS: float = _yaml_get(
        "vision", "retry_max_delay_seconds", default=2.0
    )
    VISION_CAPTION_MIN_CHARS: int = _yaml_get("vision", "caption_min_chars", default=12)
    VISION_OCR_MIN_CHARS: int = _yaml_get("vision", "ocr_min_chars", default=6)

    # Web Search (optional, for augmenting RAG with internet data)
    SEARCH_BINDING: str = _yaml_get("search", "binding") or ""
    SEARCH_API_KEY: SecretStr = SecretStr("")
    SEARCH_REGION: str = _yaml_get("search", "region") or "wt-wt"  # DuckDuckGo region
    SEARCH_MAX_RESULTS: int = _yaml_get("search", "max_results") or 5
    SEARCH_TIMEOUT: int = _yaml_get("search", "timeout") or 10

    # RAG parameters
    DISTANCE_METRIC: str = _yaml_get("rag", "distance_metric", default="l2")  # "l2" or "cosine"
    CHUNK_SIZE: int = Field(default=_yaml_get("rag", "chunk_size", default=1024), ge=64, le=8192)
    CHUNK_OVERLAP: int = Field(
        default=_yaml_get("rag", "chunk_overlap", default=128), ge=0, le=2048
    )
    TOP_K: int = _yaml_get("rag", "top_k", default=8)
    QUERY_REWRITE_ENABLED: bool = _yaml_get("rag", "query_rewrite_enabled", default=True)
    # empty = same as LLM_MODEL
    QUERY_REWRITE_MODEL: str = _yaml_get("rag", "query_rewrite_model", default="")
    QUERY_REWRITE_TIMEOUT_SECONDS: int = _yaml_get(
        "rag", "query_rewrite_timeout_seconds", default=10
    )
    SCORE_THRESHOLD: float = _yaml_get("rag", "score_threshold", default=1.5)
    RERANKER_BINDING: str = _yaml_get("rag", "reranker_binding", default="")
    RERANKER_MODEL: str = _yaml_get("rag", "reranker_model", default="cohere/rerank-4-pro")
    RERANKER_API_KEY: SecretStr = SecretStr(_yaml_get("rag", "reranker_api_key", default=""))
    RERANKER_BASE_URL: str = _yaml_get("rag", "reranker_base_url", default="")
    RERANKER_TOP_N: int = _yaml_get("rag", "reranker_top_n", default=16)
    RERANKER_MIN_SCORE: float = _yaml_get("rag", "reranker_min_score", default=0.15)
    RERANKER_TIMEOUT_SECONDS: float = _yaml_get("rag", "reranker_timeout_seconds", default=30.0)
    RERANKER_BATCH_SIZE: int = _yaml_get("rag", "reranker_batch_size", default=200)
    PARENT_CHILD_ENABLED: bool = _yaml_get("rag", "parent_child_enabled", default=False)
    CHILD_CHUNK_SIZE: int = Field(
        default=_yaml_get("rag", "child_chunk_size", default=256), ge=32, le=2048
    )
    CHILD_CHUNK_OVERLAP: int = _yaml_get("rag", "child_chunk_overlap", default=32)
    HYBRID_SEARCH_ENABLED: bool = _yaml_get("rag", "hybrid_search_enabled", default=True)
    HYBRID_ALPHA: float = _yaml_get("rag", "hybrid_alpha", default=0.5)
    HYBRID_MULTIMODAL_ALPHA: float = _yaml_get("rag", "hybrid_multimodal_alpha", default=0.5)
    RAG_RERANK_FETCH_MULTIPLIER: int = _yaml_get("rag", "rerank_fetch_multiplier", default=6)
    RAG_PERSIST_INTERVAL_SECONDS: float = _yaml_get("rag", "persist_interval_seconds", default=30.0)
    SEMANTIC_CHUNKING_ENABLED: bool = _yaml_get("rag", "semantic_chunking_enabled", default=False)
    NON_TEXT_CHUNKING_STRATEGY: str = _yaml_get(
        "rag", "non_text_chunking_strategy", default="native"
    )
    MULTI_QUERY_ENABLED: bool = _yaml_get("rag", "multi_query_enabled", default=False)
    MULTI_QUERY_COUNT: int = _yaml_get("rag", "multi_query_count", default=3)
    # Retrieval provider switch: "native" | "hybrid" | "multimodal" | "hybrid_multimodal"
    RAG_RETRIEVER_PROVIDER: str = _yaml_get("rag", "retriever_provider", default="native")
    # Enable optional RAGAnything retriever branch (partial rollout)
    RAGANYTHING_ENABLED: bool = _yaml_get("rag", "raganything_enabled", default=False)
    # If true, fall back to native retriever when RAGAnything branch fails
    RAGANYTHING_FALLBACK_TO_NATIVE: bool = _yaml_get(
        "rag", "raganything_fallback_to_native", default=True
    )
    # Working directory for RAGAnything/LightRAG storage
    RAGANYTHING_WORKING_DIR: str = _yaml_get("rag", "raganything_working_dir", default="")
    # Timeout guardrails for LightRAG/RAGAnything operations
    RAGANYTHING_INDEX_TIMEOUT_SECONDS: float = _yaml_get(
        "rag", "raganything_index_timeout_seconds", default=120.0
    )
    RAGANYTHING_QUERY_TIMEOUT_SECONDS: float = _yaml_get(
        "rag", "raganything_query_timeout_seconds", default=30.0
    )
    RAGANYTHING_LLM_TIMEOUT_SECONDS: float = _yaml_get(
        "rag", "raganything_llm_timeout_seconds", default=90.0
    )
    RAG_INDEX_TABLES: bool = _yaml_get("rag", "index_tables", default=True)
    RAG_INDEX_IMAGE_CAPTIONS: bool = _yaml_get("rag", "index_image_captions", default=True)
    RAG_IMAGE_OCR_MIN_LENGTH: int = _yaml_get("rag", "image_ocr_min_length", default=15)
    RAG_CONTENT_TYPE_RERANK_ENABLED: bool = _yaml_get(
        "rag", "content_type_rerank_enabled", default=True
    )
    RAG_SIBLING_CORETRIEVE_ENABLED: bool = _yaml_get(
        "rag", "sibling_coretrieve_enabled", default=True
    )
    RAG_SIBLING_CORETRIEVE_PER_ANCHOR: int = _yaml_get(
        "rag", "sibling_coretrieve_per_anchor", default=1
    )
    RAG_SIBLING_CORETRIEVE_MAX_TOTAL: int = _yaml_get(
        "rag", "sibling_coretrieve_max_total", default=4
    )
    # Audio segmentation with semantic boundary detection
    AUDIO_SEMANTIC_BOUNDARY_ENABLED: bool = _yaml_get(
        "rag", "audio_semantic_boundary_enabled", default=False
    )
    AUDIO_SEMANTIC_BOUNDARY_THRESHOLD: float = _yaml_get(
        "rag", "audio_semantic_boundary_threshold", default=0.5
    )
    AUDIO_SEMANTIC_MIN_SEGMENTS: int = _yaml_get("rag", "audio_semantic_min_segments", default=2)
    AUDIO_SEMANTIC_MAX_SEGMENTS: int = _yaml_get("rag", "audio_semantic_max_segments", default=20)
    AUDIO_SPEAKER_IN_CONTENT: bool = _yaml_get("rag", "audio_speaker_in_content", default=True)
    AUDIO_SPLIT_ON_SPEAKER_CHANGE: bool = _yaml_get(
        "rag", "audio_split_on_speaker_change", default=True
    )

    # Memory
    MEMORY_AUTO_EXTRACT: bool = _yaml_get("memory", "auto_extract", default=True)
    MEMORY_MAX_FACTS_PER_TURN: int = _yaml_get("memory", "max_facts_per_turn", default=3)
    MEMORY_EXTRACTION_MODEL: str = _yaml_get("memory", "extraction_model", default="")
    MEMORY_DECAY_ENABLED: bool = _yaml_get("memory", "decay_enabled", default=True)
    MEMORY_DECAY_INTERVAL_HOURS: int = Field(
        default=_yaml_get("memory", "decay_interval_hours", default=24), ge=1, le=8760
    )
    MEMORY_DECAY_RATE_PER_DAY: float = Field(
        default=_yaml_get("memory", "decay_rate_per_day", default=0.01), ge=0.0, le=1.0
    )
    MEMORY_TTL_DAYS: int = _yaml_get("memory", "ttl_days", default=90)
    MEMORY_MAX_CONTEXT_ITEMS: int = _yaml_get("memory", "max_context_items", default=6)
    MEMORY_MAX_PER_USER: int = Field(
        default=_yaml_get("memory", "max_per_user", default=500), ge=10
    )
    MEMORY_DEDUP_THRESHOLD: float = Field(
        default=_yaml_get("memory", "dedup_threshold", default=0.75), ge=0.0, le=1.0
    )
    MEMORY_INITIAL_IMPORTANCE: int = Field(
        default=_yaml_get("memory", "initial_importance", default=3), ge=1, le=5
    )
    MEMORY_MIN_IMPORTANCE: int = Field(
        default=_yaml_get("memory", "min_importance", default=1), ge=1, le=5
    )
    MEMORY_MAX_IMPORTANCE: int = Field(
        default=_yaml_get("memory", "max_importance", default=5), ge=1, le=10
    )
    MEMORY_CLUSTER_THRESHOLD: float = Field(
        default=_yaml_get("memory", "cluster_threshold", default=0.75), ge=0.0, le=1.0
    )
    MEMORY_SCORING_SEMANTIC_WEIGHT: float = Field(
        default=_yaml_get("memory", "scoring_semantic_weight", default=0.3), ge=0.0, le=1.0
    )
    MEMORY_SCORING_DECAY_WEIGHT: float = Field(
        default=_yaml_get("memory", "scoring_decay_weight", default=0.4), ge=0.0, le=1.0
    )
    MEMORY_SCORING_IMPORTANCE_WEIGHT: float = Field(
        default=_yaml_get("memory", "scoring_importance_weight", default=0.3), ge=0.0, le=1.0
    )
    SESSION_MAX_HISTORY: int = _yaml_get("memory", "session_max_history", default=50)
    SESSION_MAX_TOKENS: int = _yaml_get("memory", "session_max_tokens", default=4096)
    SESSION_SUMMARY_ENABLED: bool = _yaml_get("memory", "session_summary_enabled", default=True)
    SESSION_SUMMARY_MIN_TURNS: int = _yaml_get("memory", "session_summary_min_turns", default=4)
    SESSION_SUMMARY_MAX_ITEMS: int = _yaml_get("memory", "session_summary_max_items", default=3)
    SESSION_SUMMARY_MAX_MESSAGES: int = _yaml_get(
        "memory", "session_summary_max_messages", default=100
    )
    SESSION_SUMMARY_IDLE_MINUTES: int = _yaml_get(
        "memory", "session_summary_idle_minutes", default=15
    )
    MEMORY_CONSOLIDATION_ENABLED: bool = _yaml_get("memory", "consolidation_enabled", default=True)
    # LLM-driven memory profile refresh
    MEMORY_PROFILE_ENABLED: bool = _yaml_get("memory", "profile_enabled", default=True)
    MEMORY_PROFILE_REFRESH_INTERVAL: int = _yaml_get(
        "memory", "profile_refresh_interval", default=50
    )
    MEMORY_CONSOLIDATION_MIN_CLUSTER: int = _yaml_get(
        "memory", "consolidation_min_cluster", default=3
    )
    # Window (days) for considering recently-updated memories as consolidation seeds.
    # Older memories can still be paired with seeds but won't initiate consolidation.
    MEMORY_CONSOLIDATION_WINDOW_DAYS: int = _yaml_get(
        "memory", "consolidation_window_days", default=2
    )
    # Auto-extracted facts start at a lower importance (1 instead of 3) so they
    # don't compete equally with manually-entered facts (CRITICAL-4).
    MEMORY_AUTO_EXTRACT_INITIAL_IMPORTANCE: float = _yaml_get(
        "memory", "auto_extract_initial_importance", default=1.0
    )
    # When True, include existing memory snippets in the LLM extraction prompt
    # to help the model avoid duplicates.  When False (or when using a provider
    # that logs prompts), existing memories are omitted to reduce data exposure.
    MEMORY_EXTRACTION_INCLUDE_EXISTING: bool = _yaml_get(
        "memory", "extraction_include_existing", default=True
    )
    # Use Chroma semantic similarity for memory clustering (falls back to text overlap)
    MEMORY_SEMANTIC_CLUSTER_ENABLED: bool = _yaml_get(
        "memory", "semantic_cluster_enabled", default=True
    )
    # Knowledge graph entity extraction
    KNOWLEDGE_GRAPH_ENABLED: bool = _yaml_get("memory", "knowledge_graph_enabled", default=True)
    # Cosine similarity threshold for merging entity aliases into canonical entities.
    # Tune this when changing embedding models — different models produce different
    # similarity distributions.
    ENTITY_ALIAS_MERGE_THRESHOLD: float = _yaml_get(
        "memory", "entity_alias_merge_threshold", default=0.85
    )
    # RRF k parameter (Cormack et al. 2009). Higher = more weight on rank position.
    RRF_K_PARAM: int = _yaml_get("rag", "rrf_k", default=60)
    # Separate min_score thresholds for scoped vs unscoped reranker post-filter.
    # Unscoped queries are broad — softer threshold avoids silently dropping all results.
    RERANKER_UNSCOPED_MIN_SCORE: float = _yaml_get(
        "rag", "reranker_unscoped_min_score", default=0.05
    )
    RERANKER_SCOPED_MIN_SCORE: float = _yaml_get("rag", "reranker_scoped_min_score", default=0.10)
    # Extraction aggressiveness: "precise" | "balanced" | "aggressive"
    MEMORY_EXTRACTION_MODE: str = _yaml_get("memory", "extraction_mode", default="balanced")
    # Maximum relations returned per entity (outgoing + incoming combined)
    ENTITY_RELATIONS_LIMIT: int = _yaml_get("memory", "entity_relations_limit", default=50)

    # Server
    ENVIRONMENT: str = _yaml_get("server", "environment", default="dev")
    # 0.0.0.0 is the standard bind for containerized deployments.
    HOST: str = _yaml_get("server", "host", default="0.0.0.0")  # nosec B104
    PORT: int = _yaml_get("server", "port", default=8000)
    CORS_ORIGINS: str = _yaml_get("server", "cors_origins", default="")
    TRUSTED_PROXIES: str = _yaml_get("server", "trusted_proxies", default="")
    API_KEY: SecretStr = SecretStr(_yaml_get("server", "api_key", default=""))
    # Comma-separated legacy API keys used only for idempotency payload decryption during rotation.
    IDEMPOTENCY_OLD_KEYS: str = ""

    # Security headers & trusted hosts
    TRUSTED_HOSTS: str = _yaml_get("server", "trusted_hosts", default="")
    SECURITY_HEADERS_ENABLED: bool = _yaml_get("server", "security_headers_enabled", default=True)
    SECURITY_HSTS_MAX_AGE: int = _yaml_get("server", "security_hsts_max_age", default=31536000)
    SECURITY_FRAME_OPTIONS: str = _yaml_get("server", "security_frame_options", default="DENY")
    SECURITY_REFERRER_POLICY: str = _yaml_get(
        "server", "security_referrer_policy", default="strict-origin-when-cross-origin"
    )
    SECURITY_CSP: str = _yaml_get(
        "server",
        "security_csp",
        default="default-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self';",
    )

    # Parser limits
    MAX_PARSE_PAGES: int = _yaml_get("parser", "max_parse_pages", default=1000)
    PARSE_TIMEOUT_SECONDS: int = _yaml_get("parser", "parse_timeout_seconds", default=900)
    PARSE_TIMEOUT_PER_MB_SECONDS: int = _yaml_get(
        "parser", "parse_timeout_per_mb_seconds", default=2
    )
    PARSE_TIMEOUT_MAX_SECONDS: int = _yaml_get("parser", "parse_timeout_max_seconds", default=900)
    PARSER_MAX_IMAGES_PER_PAGE: int = _yaml_get("parser", "max_images_per_page", default=20)
    PARSER_MAX_IMAGE_BYTES: int = _yaml_get("parser", "max_image_bytes", default=8_388_608)
    DOC_CLEAN_REPETITION_MIN_PAGES: int = _yaml_get(
        "parser", "doc_clean_repetition_min_pages", default=3
    )
    DOC_CLEAN_REPETITION_MIN_RATIO: float = _yaml_get(
        "parser", "doc_clean_repetition_min_ratio", default=0.6
    )
    DOC_CLEAN_HEADER_FOOTER_MAX_LINES: int = _yaml_get(
        "parser", "doc_clean_header_footer_max_lines", default=2
    )
    DOC_CLEAN_REPETITION_MAX_LINE_LENGTH: int = _yaml_get(
        "parser", "doc_clean_repetition_max_line_length", default=120
    )

    # Upload
    MAX_UPLOAD_SIZE_MB: int = Field(
        default=_yaml_get("upload", "max_size_mb", default=500), ge=1, le=10000
    )
    MEETING_AUTO_SUMMARIZE_FILES: bool = _yaml_get(
        "upload", "meeting_auto_summarize_files", default=True
    )
    PER_FILE_SUMMARY_INPUT_MAX_TOKENS: int = _yaml_get(
        "upload", "per_file_summary_input_max_tokens", default=8000
    )
    EXTRACTION_INPUT_MAX_TOKENS: int = _yaml_get(
        "memory", "extraction_input_max_tokens", default=1500
    )
    EXTRACTION_MIN_ANSWER_CHARS: int = _yaml_get(
        "memory", "extraction_min_answer_chars", default=50
    )
    COMBINED_EXTRACTION_ENABLED: bool = _yaml_get(
        "memory", "combined_extraction_enabled", default=True
    )
    MULTIMODAL_CAPTIONING_ENABLED: bool = _yaml_get(
        "upload", "multimodal_captioning_enabled", default=True
    )
    VISION_COMBINED_EXTRACTION_ENABLED: bool = _yaml_get(
        "upload", "vision_combined_extraction_enabled", default=True
    )
    MULTIMODAL_CAPTION_OCR_DEDUP_ENABLED: bool = _yaml_get(
        "upload", "multimodal_caption_ocr_dedup_enabled", default=True
    )
    MULTIMODAL_CAPTION_OCR_DEDUP_TIMEOUT_SECONDS: float = _yaml_get(
        "upload", "multimodal_caption_ocr_dedup_timeout_seconds", default=8.0
    )
    VIDEO_KEYFRAMES_ENABLED: bool = _yaml_get("upload", "video_keyframes_enabled", default=False)

    # Non-meeting context token caps (prevent unscoped context from crowding out meeting chunks)
    MEMORY_CONTEXT_MAX_TOKENS: int = _yaml_get("rag", "memory_context_max_tokens", default=800)
    ENTITY_CONTEXT_MAX_TOKENS: int = _yaml_get("rag", "entity_context_max_tokens", default=600)
    SESSION_CONTEXT_MAX_TOKENS: int = _yaml_get("rag", "session_context_max_tokens", default=800)
    CONTEXT_LOAD_TIMEOUT_S: float = _yaml_get("rag", "context_load_timeout_s", default=3.0)
    STREAM_CONCURRENT_LIMIT: int = _yaml_get("api", "stream_concurrent_limit", default=20)
    WEB_SEARCH_TIMEOUT_S: float = _yaml_get("search", "web_search_timeout_s", default=8.0)
    SKILL_MATCH_TIMEOUT_S: float = _yaml_get("rag", "skill_match_timeout_s", default=2.5)
    SKILL_ROUTE_TIMEOUT_S: float = _yaml_get("rag", "skill_route_timeout_s", default=8.0)
    SKILL_MATCHING_ENABLED: bool = _yaml_get("rag", "skill_matching_enabled", default=True)
    SKILL_ROUTING_MIN_SIMILARITY: float = _yaml_get(
        "rag", "skill_routing_min_similarity", default=0.35
    )
    SEMANTIC_EMBED_TIMEOUT_S: float = _yaml_get("rag", "semantic_embed_timeout_s", default=5.0)
    # Max global (unscoped) memory entries when meeting selection is active
    GLOBAL_MEMORY_LIMIT: int = _yaml_get("memory", "global_memory_limit", default=3)
    # When a scope (meeting_ids/file_ids) is active, exclude all untagged
    # memories/entities except explicit user-profile ones (category="user_profile").
    # This prevents cross-meeting context pollution. Default False for back-compat.
    SCOPED_MEMORY_STRICT: bool = _yaml_get("memory", "scoped_memory_strict", default=False)
    # How much we over-fetch from the memory/entity vector store before
    # applying scope post-filter. With k*factor candidates we can reliably
    # find k scope-matching entries even if the top results are dominated by
    # out-of-scope memories.
    MEMORY_SEARCH_OVERSAMPLE_FACTOR: int = _yaml_get(
        "memory", "search_oversample_factor", default=5
    )
    # Enable per-meeting "sharding" of retrieval: when no meeting is selected
    # and > 1 meeting exists, oversample and cap contribution from any single
    # meeting so broad queries don't collapse onto one meeting's chunks.
    UNSCOPED_DIVERSITY_ENABLED: bool = _yaml_get("rag", "unscoped_diversity_enabled", default=True)
    # Max chunks any single meeting may contribute to the unscoped retrieval
    # pool before rerank.
    UNSCOPED_MAX_PER_MEETING: int = _yaml_get("rag", "unscoped_max_per_meeting", default=5)
    # Over-fetch multiplier for unscoped-diversity mode.
    UNSCOPED_FETCH_MULTIPLIER: int = _yaml_get("rag", "unscoped_fetch_multiplier", default=4)
    SESSION_SUMMARY_STARTUP_BACKFILL: bool = _yaml_get(
        "memory", "session_summary_startup_backfill", default=False
    )
    SESSION_CONTEXT_SKIP_THRESHOLD: int = _yaml_get(
        "memory", "session_context_skip_threshold", default=3
    )

    # Fallback circuit breaker (for LLM error handling)
    FALLBACK_BREAKER_THRESHOLD: int = _yaml_get("llm", "fallback_breaker_threshold", default=3)
    FALLBACK_BREAKER_COOLDOWN_SECONDS: float = _yaml_get(
        "llm", "fallback_breaker_cooldown_seconds", default=30.0
    )

    # Hierarchical (funnel) RAG: meetings → files → chunks
    RAG_HIERARCHICAL_ENABLED: bool = _yaml_get("rag", "hierarchical_enabled", default=True)
    RAG_FUNNEL_FETCH_MULTIPLIER: int = _yaml_get("rag", "funnel_fetch_multiplier", default=10)
    RAG_FUNNEL_TOP_MEETINGS: int = _yaml_get("rag", "funnel_top_meetings", default=8)
    RAG_FUNNEL_TOP_FILES: int = _yaml_get("rag", "funnel_top_files", default=12)
    RAG_FUNNEL_MIN_POOL_SIZE: int = _yaml_get("rag", "funnel_min_pool_size", default=12)
    RAG_FUNNEL_AGGREGATION: str = _yaml_get("rag", "funnel_aggregation", default="top_k_mean")
    RAG_FUNNEL_AGG_TOP_K: int = _yaml_get("rag", "funnel_agg_top_k", default=3)
    RAG_FUNNEL_TITLE_PRIOR_ENABLED: bool = _yaml_get(
        "rag", "funnel_title_prior_enabled", default=True
    )
    RAG_FUNNEL_TITLE_PRIOR_WEIGHT: float = _yaml_get(
        "rag", "funnel_title_prior_weight", default=0.05
    )
    RAG_FUNNEL_TITLE_PRIOR_CAP: float = _yaml_get("rag", "funnel_title_prior_cap", default=0.15)
    # File-level title prior (used by funnel_narrow stage to boost files whose
    # parent meeting title matches the query).  Mirrors the meeting-level
    # title prior used by hierarchical funnel.
    RAG_FUNNEL_FILE_PRIOR_ENABLED: bool = _yaml_get(
        "rag", "funnel_file_prior_enabled", default=True
    )
    RAG_FUNNEL_FILE_PRIOR_WEIGHT: float = _yaml_get("rag", "funnel_file_prior_weight", default=0.10)
    RAG_FUNNEL_FILE_PRIOR_CAP: float = _yaml_get("rag", "funnel_file_prior_cap", default=0.30)
    # Bonus when the query *fully contains* the title (or vice versa) — a strong
    # signal that the user is asking about that specific document.  Stacks on
    # top of per-token boosts but the sum is still capped at FILE_PRIOR_CAP.
    RAG_FUNNEL_FILE_PRIOR_FULL_MATCH_BONUS: float = _yaml_get(
        "rag", "funnel_file_prior_full_match_bonus", default=0.20
    )
    # How the file-level title prior is applied to the aggregated score.
    # ``additive``      — prior is added to score (default, back-compat)
    # ``multiplicative`` — score is multiplied by (1 + prior), stronger effect
    RAG_FUNNEL_FILE_PRIOR_MODE: str = _yaml_get("rag", "funnel_file_prior_mode", default="additive")

    # Conversational anchor (session-level retrieval scope hint)
    RAG_ANCHOR_ENABLED: bool = _yaml_get("rag", "anchor_enabled", default=True)
    RAG_ANCHOR_TTL_MINUTES: int = _yaml_get("rag", "anchor_ttl_minutes", default=30)
    # TTL semantics: ``fixed`` expires N minutes after creation; ``sliding``
    # refreshes the timestamp each time the anchor is read.  Sliding suits long
    # sessions where the anchor stays relevant as long as the user keeps
    # asking related questions.
    RAG_ANCHOR_TTL_MODE: str = _yaml_get("rag", "anchor_ttl_mode", default="fixed")
    RAG_ANCHOR_NARROW_FETCH_MULTIPLIER: int = _yaml_get(
        "rag", "anchor_narrow_fetch_multiplier", default=5
    )
    RAG_ANCHOR_NARROW_FETCH_MULTIPLIER_CASE2: int = _yaml_get(
        "rag", "anchor_narrow_fetch_multiplier_case2", default=3
    )
    RAG_ANCHOR_NARROW_RRF_WEIGHT: float = _yaml_get("rag", "anchor_narrow_rrf_weight", default=0.5)
    RAG_ANCHOR_MAX_IDS: int = _yaml_get("rag", "anchor_max_ids", default=8)
    RAG_ANCHOR_BOOST_IN_BROAD_RECALL: bool = _yaml_get(
        "rag", "anchor_boost_in_broad_recall", default=True
    )
    RAG_ANCHOR_QUOTA_RATIO: float = Field(
        default=_yaml_get("rag", "anchor_quota_ratio", default=0.5), ge=0.0, le=1.0
    )
    # Score given to anchor-only files (no router/funnel evidence) as a
    # fraction of the median normalised score in the current scope.  Lower
    # values prevent anchor files from monopolising chunk-budget allocation
    # and reranker tiebreaks.  0 disables the heuristic; 1.0 falls back to
    # the legacy "anchor file wins everything" behaviour.
    RAG_ANCHOR_ONLY_SCORE_FLOOR_RATIO: float = Field(
        default=_yaml_get("rag", "anchor_only_score_floor_ratio", default=0.8),
        ge=0.0,
        le=1.0,
    )

    # Funnel narrow calibration in broad recall
    # Evidence floor for chunk-aggregated file scores.  Interpretation depends
    # on RAG_FUNNEL_EVIDENCE_MODE.
    RAG_FUNNEL_NARROW_MIN_EVIDENCE: float = _yaml_get(
        "rag", "funnel_narrow_min_evidence", default=0.15
    )
    # ``absolute``  — keep files whose aggregated score >= MIN_EVIDENCE (legacy)
    # ``ratio``     — keep files whose score >= MIN_EVIDENCE * max_score_in_pool
    # ``percentile``— keep files in the top (100 - MIN_EVIDENCE*100) percentile
    # ``ratio`` is robust to embedding/distance scale shifts and is now the default.
    RAG_FUNNEL_EVIDENCE_MODE: str = _yaml_get("rag", "funnel_evidence_mode", default="ratio")
    # Cap on final scope file count after funnel narrow (includes anchor files).
    # Distinct from BROAD_RECALL_MAX_FILES (which is the SQL LIMIT for legacy
    # enumerate fallback) to avoid coupling two different semantics.
    RAG_BROAD_RECALL_SCOPE_CAP: int = _yaml_get("rag", "broad_recall_scope_cap", default=8)
    # Strategy used to merge router-selected files with funnel candidates.
    # ``rrf`` (default) — Reciprocal Rank Fusion; high-scoring funnel files are
    #   not penalised by the strict zigzag ordering when there is no overlap.
    # ``zigzag`` — legacy v3 behaviour kept for one-line rollback.
    RAG_FUNNEL_MERGE_STRATEGY: str = _yaml_get("rag", "funnel_merge_strategy", default="rrf")
    # K constant for Reciprocal Rank Fusion when merging router + funnel files.
    # Lower values amplify rank differences; 60 is the standard literature default.
    RAG_FUNNEL_RRF_K: int = _yaml_get("rag", "funnel_rrf_k", default=60)
    # Aggregation mixing: alpha * top_k_mean + (1 - alpha) * log_chunk_count.
    # alpha=1.0 disables chunk-count blending (top_k_mean only); 0.85 gives
    # large dense files a small fairness boost without dominating quality.
    RAG_FUNNEL_AGGREGATION_ALPHA: float = Field(
        default=_yaml_get("rag", "funnel_aggregation_alpha", default=0.85),
        ge=0.0,
        le=1.0,
    )
    # Adaptive wide_k bounds.  When set, wide_k = clamp(MIN, base * log_factor, MAX)
    # where ``base = TOP_K * RAG_FUNNEL_FETCH_MULTIPLIER`` and ``log_factor``
    # scales with the in-scope chunk pool size.  Set both to 0 to disable
    # adaptive sizing and use the legacy fixed value.
    RAG_FUNNEL_WIDE_K_MIN: int = _yaml_get("rag", "funnel_wide_k_min", default=0)
    RAG_FUNNEL_WIDE_K_MAX: int = _yaml_get("rag", "funnel_wide_k_max", default=0)
    # Multimodal funnel: include the multimodal store in the wide fetch when
    # RAGANYTHING_ENABLED.  Improves recall for image/table-heavy files.
    RAG_FUNNEL_MULTIMODAL_ENABLED: bool = _yaml_get(
        "rag", "funnel_multimodal_enabled", default=True
    )
    # File-level RRF when merging multi-query variants in broad recall.  When
    # broad-recall multi-query is enabled, each variant runs router+funnel and
    # the resulting scopes are merged via file-level RRF (same K as
    # RAG_FUNNEL_RRF_K).  Set to ``zigzag`` to fall back to legacy behaviour.
    RAG_BROAD_RECALL_MQ_MERGE: str = _yaml_get("rag", "broad_recall_mq_merge", default="rrf")
    # Pre-rerank ngram dedup (cheap pass to drop near-clones before paying
    # Cohere/BGE rerank cost).  Threshold is intentionally higher than the
    # post-rerank pass to avoid dropping nuanced near-duplicates that the
    # reranker would have re-ordered.
    RAG_PRE_RERANK_DEDUP_ENABLED: bool = _yaml_get("rag", "pre_rerank_dedup_enabled", default=True)
    RAG_PRE_RERANK_DEDUP_THRESHOLD: float = Field(
        default=_yaml_get("rag", "pre_rerank_dedup_threshold", default=0.92),
        ge=0.0,
        le=1.0,
    )
    # Push speaker filter into the Chroma where clause when query analysis
    # detects a high-confidence speaker.  Disabled (post-filter only) by
    # default for back-compat; flip on to skip retrieving chunks that the
    # post-filter would discard anyway.
    RAG_SPEAKER_FILTER_PUSHDOWN: bool = _yaml_get("rag", "speaker_filter_pushdown", default=False)
    # Broad recall mode runs an independent funnel narrow that already widens
    # the candidate pool via wide-fetch + chunk evidence.  Multi-query variants
    # in this mode hit the same wide-fetch cache and produce duplicate chunks,
    # which dedup collapses, so multi-query loses its diversity benefit.
    # Default off in broad recall; flip to ``true`` only after confirming a
    # benchmark uplift on your corpus.
    RAG_BROAD_RECALL_MULTI_QUERY_ENABLED: bool = _yaml_get(
        "rag", "broad_recall_multi_query_enabled", default=False
    )

    # Broad recall mode (active when no files selected, with or without meetings)
    RAG_MIN_CHUNKS_PER_FILE: int = _yaml_get("rag", "min_chunks_per_file", default=3)
    RAG_FAIR_ADAPTIVE_CHUNKS: bool = _yaml_get("rag", "fair_adaptive_chunks", default=True)
    RAG_FAIR_SIZE_FACTOR_ENABLED: bool = _yaml_get("rag", "fair_size_factor_enabled", default=True)
    RAG_FAIR_CONCURRENCY: int = _yaml_get("rag", "fair_concurrency", default=8)
    # SQL LIMIT for legacy enumerate fallback (list_recent_ready_file_ids when
    # no meeting filter is given).  Default 50 preserves the original behaviour.
    BROAD_RECALL_MAX_FILES: int = _yaml_get("rag", "broad_recall_max_files", default=50)
    # Floor for effective_k when the user has pinned a meeting but no specific files.
    TOP_K_MEETING_SCOPED_FLOOR: int = _yaml_get("rag", "top_k_meeting_scoped_floor", default=16)
    SUMMARY_INTENT_TOP_K: int = _yaml_get("rag", "summary_intent_top_k", default=12)

    # File scoping strategy for broad recall.
    #   "router_and_funnel" — summary router + funnel parallel, zigzag merge (v4 default)
    #   "funnel_only"       — skip summary router, funnel does all file selection
    #   "router_pre_filter" — router narrows meeting scope first, funnel within those meetings
    #   "router_only"       — router selects files directly (no funnel narrow)
    RAG_FILE_SCOPING_MODE: str = _yaml_get("rag", "file_scoping_mode", default="router_and_funnel")

    # Summary-vector router: pre-narrow files via per-file summary embedding
    # similarity before chunk-level retrieval.
    RAG_SUMMARY_ROUTER_ENABLED: bool = _yaml_get("rag", "summary_router_enabled", default=True)
    RAG_SUMMARY_ROUTER_TOP_FILES: int = _yaml_get("rag", "summary_router_top_files", default=12)
    RAG_SUMMARY_ROUTER_MIN_SCORE: float = _yaml_get("rag", "summary_router_min_score", default=0.0)
    RAG_SUMMARY_ROUTER_FALLBACK_TO_CHUNK: bool = _yaml_get(
        "rag", "summary_router_fallback_to_chunk", default=True
    )
    RAG_SUMMARY_ROUTER_HYBRID_ENABLED: bool = _yaml_get(
        "rag", "summary_router_hybrid_enabled", default=True
    )
    RAG_SUMMARY_ROUTER_HYBRID_ALPHA: float = _yaml_get(
        "rag", "summary_router_hybrid_alpha", default=0.6
    )

    # Meeting-summary vector router: pre-narrow meetings via meeting-summary
    # embedding similarity BEFORE file-level routing.  Runs only in unscoped
    # (no meeting selected) mode to avoid unnecessary work.
    RAG_MEETING_SUMMARY_ROUTER_ENABLED: bool = _yaml_get(
        "rag", "meeting_summary_router_enabled", default=True
    )
    RAG_MEETING_SUMMARY_ROUTER_TOP_MEETINGS: int = _yaml_get(
        "rag", "meeting_summary_router_top_meetings", default=10
    )
    RAG_MEETING_SUMMARY_ROUTER_MIN_SCORE: float = _yaml_get(
        "rag", "meeting_summary_router_min_score", default=0.0
    )
    RAG_MEETING_SUMMARY_ROUTER_MIN_HITS: int = _yaml_get(
        "rag", "meeting_summary_router_min_hits", default=1
    )
    # Summary context truncation budgets (chars).  These cap how much of each
    # summary is injected into the LLM prompt.  Dial back for token-constrained
    # models; increase for large-context models.
    FILE_SUMMARY_CONTEXT_CHARS: int = _yaml_get("rag", "file_summary_context_chars", default=800)
    MEETING_SUMMARY_CONTEXT_CHARS: int = _yaml_get(
        "rag", "meeting_summary_context_chars", default=1600
    )
    # Broad-inject caps: when ≤ this many meetings/files exist, ALL summaries are
    # injected without routing.  Exceeding the cap falls back to top-N routing.
    MEETING_SUMMARY_BROAD_INJECT_CAP: int = _yaml_get(
        "rag", "meeting_summary_broad_inject_cap", default=20
    )
    FILE_SUMMARY_BROAD_INJECT_CAP: int = _yaml_get(
        "rag", "file_summary_broad_inject_cap", default=50
    )
    # Per-router deadline for meeting summary routing.  Timeout is treated as
    # fail-open (all meetings considered).  Set to 0 to disable.
    RAG_MEETING_SUMMARY_ROUTER_TIMEOUT_S: float = _yaml_get(
        "rag", "meeting_summary_router_timeout_s", default=1.5
    )

    # History-aware query resolver
    RESOLVER_ENABLED: bool = _yaml_get("rag", "resolver_enabled", default=True)
    RESOLVER_HISTORY_TURNS: int = _yaml_get("rag", "resolver_history_turns", default=3)
    RESOLVER_HISTORY_TOKEN_BUDGET: int = _yaml_get(
        "rag", "resolver_history_token_budget", default=1500
    )
    RESOLVER_TIMEOUT_S: float = _yaml_get("rag", "resolver_timeout_s", default=4.0)

    # Data retention
    CHAT_MESSAGE_RETENTION_DAYS: int = _yaml_get("retention", "chat_message_days", default=180)
    DECAY_STATE_RETENTION_DAYS: int = _yaml_get("retention", "decay_state_days", default=365)

    # Hard cap of 5 GB to prevent runaway upload sizes from exhausting disk.
    # Even if MAX_UPLOAD_SIZE_MB is configured higher, uploads are capped here.
    _UPLOAD_HARD_CAP_BYTES: int = 5 * 1024 * 1024 * 1024  # 5 GB

    @property
    def MAX_UPLOAD_BYTES(self) -> int:
        return min(
            self.MAX_UPLOAD_SIZE_MB * 1024 * 1024,
            self._UPLOAD_HARD_CAP_BYTES,
        )

    # Local LLM providers that don't require an API key
    _LOCAL_LLM_PROVIDERS = frozenset({"ollama", "lm_studio", "vllm", "llama_cpp"})

    def model_post_init(self, __context: object) -> None:
        """Ensure required directories exist and validate critical settings."""
        import logging
        import os

        _logger = logging.getLogger(__name__)

        # Validate ENVIRONMENT
        valid_envs = ("dev", "staging", "prod")
        if self.ENVIRONMENT not in valid_envs:
            raise ValueError(
                f"Invalid ENVIRONMENT={self.ENVIRONMENT!r}. Must be one of {valid_envs}"
            )

        # Create required directories
        self.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        self.VECTOR_DB_DIR.mkdir(parents=True, exist_ok=True)

        # Verify directories are writable
        for dir_path, label in [
            (self.UPLOAD_DIR, "UPLOAD_DIR"),
            (self.VECTOR_DB_DIR, "VECTOR_DB_DIR"),
        ]:
            if not os.access(dir_path, os.W_OK):
                _logger.critical("%s (%s) is not writable", label, dir_path)
                raise PermissionError(f"{label} is not writable")

        # Reranker validation: cohere binding requires either RERANKER_API_KEY
        # or a fallback LLM_API_KEY (the cohere client falls back to it).
        # In production this is fatal; in dev we log a warning so misconfigured
        # rerankers degrade silently to "no rerank" instead of crashing requests.
        reranker_binding = (self.RERANKER_BINDING or "").lower()
        if reranker_binding == "cohere":
            has_reranker_key = bool(self.RERANKER_API_KEY.get_secret_value())
            has_llm_fallback_key = bool(self.LLM_API_KEY.get_secret_value())
            if not has_reranker_key and not has_llm_fallback_key:
                msg = (
                    "RERANKER_BINDING=cohere requires RERANKER_API_KEY (or "
                    "LLM_API_KEY as fallback). Set one or change "
                    "RERANKER_BINDING to '' / 'bge'."
                )
                if self.ENVIRONMENT == "dev":
                    logging.getLogger(__name__).warning(msg)
                else:
                    raise ValueError(msg)

        # RAG broad-recall x funnel-narrow cross-field validation
        from ._config_validation import validate_rag_settings

        validate_rag_settings(self)

        # Production-specific validation
        if self.ENVIRONMENT != "dev":
            if not self.API_KEY.get_secret_value():
                raise ValueError("API_KEY must be set in non-dev environments")

            if not self.PRINCIPAL_PEPPER.get_secret_value():
                raise ValueError(
                    "PRINCIPAL_PEPPER must be set in non-dev environments. "
                    'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'
                )

            # CORS origins: in production, CORS_ORIGINS must be explicitly configured
            origins = [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]
            if not origins:
                raise ValueError(
                    "CORS_ORIGINS must be configured when ENVIRONMENT is not dev "
                    f"(current: {self.ENVIRONMENT})."
                )

            effective_binding = self.LLM_BINDING
            if (
                effective_binding not in self._LOCAL_LLM_PROVIDERS
                and not self.LLM_HOST
                and not self.LLM_API_KEY.get_secret_value()
            ):
                raise ValueError(
                    f"LLM_API_KEY must be set for non-local provider "
                    f"'{effective_binding}' in {self.ENVIRONMENT} mode"
                )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()


def reload_settings() -> None:
    """Reload YAML config from disk and refresh the global settings object.

    Environment variables still take precedence over YAML values.
    All modules holding a reference to ``settings`` will see updated values.
    """
    reload_yaml_config()
    new_settings = Settings()
    for field_name in Settings.model_fields:
        setattr(settings, field_name, getattr(new_settings, field_name))
    # Re-run directory creation in case paths changed
    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    settings.VECTOR_DB_DIR.mkdir(parents=True, exist_ok=True)
