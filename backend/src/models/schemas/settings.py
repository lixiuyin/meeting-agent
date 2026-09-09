"""Settings-related Pydantic models."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class LLMSettings(BaseModel):
    binding: str = Field(..., description="LLM provider binding")
    model: str = Field(..., description="Model name")
    api_key: str = Field(default="", description="API key (masked for display)")
    base_url: str = Field(default="", description="Base URL for API")
    host: str = Field(
        default="", description="Host URL for local providers (ollama, lm_studio, etc.)"
    )
    temperature: float = Field(..., ge=0.0, le=2.0, description="Sampling temperature")
    max_tokens: int = Field(..., ge=1, le=16384, description="Maximum tokens to generate")


class EmbeddingSettings(BaseModel):
    binding: str = Field(..., description="Embedding provider binding")
    model: str = Field(..., description="Model name")
    api_key: str = Field(default="", description="API key (masked for display)")
    base_url: str = Field(default="", description="Base URL for API")
    host: str = Field(default="", description="Host URL for local embedding providers")
    dimension: int = Field(..., ge=128, le=4096, description="Embedding dimension")


class RAGSettings(BaseModel):
    chunk_size: int = Field(..., ge=256, le=8192, description="Document chunk size")
    chunk_overlap: int = Field(..., ge=0, le=2048, description="Chunk overlap size")
    chunk_size_tokens: int = Field(
        default=384, ge=64, le=4096, description="Language-neutral document chunk size"
    )
    chunk_overlap_tokens: int = Field(
        default=64, ge=0, le=1024, description="Document chunk overlap in tokens"
    )
    top_k: int = Field(..., ge=1, le=50, description="Number of top results to retrieve")
    query_rewrite_enabled: bool = Field(..., description="Enable query rewriting")
    query_rewrite_model: str = Field(
        default="", description="Model for query rewriting (empty=same as LLM)"
    )
    score_threshold: float = Field(..., ge=0.0, le=10.0, description="Minimum similarity score")
    distance_metric: str = Field(
        default="l2", pattern="^(l2|cosine|ip)$", description="Vector distance metric"
    )
    reranker_binding: str = Field(..., description="Reranker provider (empty=disabled)")
    reranker_model: str = Field(default="cohere/rerank-4-pro", description="Reranker model name")
    reranker_api_key: str = Field(
        default="", description="API key for reranker (masked for display)"
    )
    reranker_base_url: str = Field(default="", description="Base URL for reranker API")
    reranker_top_n: int = Field(..., ge=1, le=20, description="Number of results after reranking")
    reranker_min_score: float = Field(
        default=0.15, ge=0.0, le=1.0, description="Minimum reranker score to keep"
    )
    reranker_timeout_seconds: float = Field(
        default=30.0, ge=1.0, le=300.0, description="Reranker timeout in seconds"
    )
    fetch_multiplier: int = Field(
        default=3, ge=1, le=10, description="Fetch multiplier before reranking"
    )
    persist_interval_seconds: float = Field(
        default=30.0, ge=1.0, le=300.0, description="Vector persist interval"
    )
    parent_child_enabled: bool = Field(..., description="Enable parent-child chunking")
    child_chunk_size: int = Field(..., ge=64, le=2042, description="Child chunk size")
    child_chunk_overlap: int = Field(..., ge=0, le=512, description="Child chunk overlap")
    child_chunk_size_tokens: int = Field(
        default=160, ge=32, le=2048, description="Child chunk size in tokens"
    )
    child_chunk_overlap_tokens: int = Field(
        default=24, ge=0, le=512, description="Child chunk overlap in tokens"
    )
    hybrid_search_enabled: bool = Field(..., description="Enable hybrid search (vector + BM25)")
    hybrid_alpha: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Vector weight for hybrid fusion: 0=pure BM25, 1=pure vector",
    )
    retriever_provider: str = Field(
        ...,
        pattern="^(vector|native|hybrid|multimodal|hybrid_multimodal)$",
        description="Default retrieval strategy: vector|hybrid|multimodal|hybrid_multimodal",
    )
    raganything_enabled: bool = Field(..., description="Enable RAGAnything multimodal retrieval")
    raganything_fallback_to_native: bool = Field(
        ...,
        description="Fallback to vector/hybrid retrieval when RAGAnything branch fails",
    )
    raganything_working_dir: str = Field(default="", description="RAGAnything working directory")
    raganything_index_timeout_seconds: float = Field(
        default=120.0, ge=10.0, le=600.0, description="RAGAnything index timeout"
    )
    raganything_query_timeout_seconds: float = Field(
        default=30.0, ge=5.0, le=300.0, description="RAGAnything query timeout"
    )
    raganything_llm_timeout_seconds: float = Field(
        default=90.0, ge=10.0, le=600.0, description="RAGAnything LLM timeout"
    )
    semantic_chunking_enabled: bool = Field(default=False, description="Enable semantic chunking")
    non_text_chunking_strategy: str = Field(
        default="native",
        pattern="^(native|text)$",
        description="How non-text files are chunked: native|text",
    )
    multi_query_enabled: bool = Field(default=False, description="Enable multi-query expansion")
    multi_query_count: int = Field(default=3, ge=1, le=10, description="Number of query variants")
    index_tables: bool = Field(default=True, description="Index into Chroma tables")
    index_image_captions: bool = Field(default=True, description="Index image captions")
    image_ocr_min_length: int = Field(
        default=15, ge=1, le=200, description="Min chars for OCR to keep"
    )
    content_type_rerank_enabled: bool = Field(
        default=True, description="Enable content-type-based reranking"
    )
    sibling_coretrieve_enabled: bool = Field(default=True, description="Enable sibling coretrieve")
    sibling_coretrieve_per_anchor: int = Field(
        default=1, ge=1, le=10, description="Siblings per anchor"
    )
    sibling_coretrieve_max_total: int = Field(
        default=4, ge=1, le=20, description="Max sibling results"
    )
    audio_semantic_boundary_enabled: bool = Field(
        default=False, description="Enable audio semantic boundary detection"
    )
    audio_semantic_boundary_threshold: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Semantic boundary threshold"
    )
    audio_semantic_min_segments: int = Field(
        default=2, ge=1, le=50, description="Min segments after split"
    )
    audio_semantic_max_segments: int = Field(
        default=20, ge=2, le=200, description="Max segments after split"
    )
    speaker_in_content: bool = Field(default=True, description="Include speaker labels in content")
    split_on_speaker_change: bool = Field(
        default=True, description="Split audio chunks at speaker transitions"
    )

    @field_validator("retriever_provider")
    @classmethod
    def validate_retriever_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized == "raganything":
            raise ValueError("retriever_provider 'raganything' is deprecated; use 'multimodal'")
        if normalized == "native":
            return "vector"
        if normalized not in {"vector", "hybrid", "multimodal", "hybrid_multimodal"}:
            raise ValueError(
                "retriever_provider must be one of: vector, hybrid, multimodal, hybrid_multimodal"
            )
        return normalized

    @field_validator("non_text_chunking_strategy")
    @classmethod
    def validate_non_text_chunking_strategy(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"native", "text"}:
            raise ValueError("non_text_chunking_strategy must be one of: native, text")
        return normalized


class MemorySettings(BaseModel):
    auto_extract: bool = Field(..., description="Auto-extract facts from conversations")
    max_facts_per_turn: int = Field(..., ge=1, le=10, description="Max facts to extract per turn")
    decay_enabled: bool = Field(..., description="Enable importance decay over time")
    decay_interval_hours: int = Field(
        default=24, ge=1, le=168, description="Decay check interval in hours"
    )
    ttl_days: int = Field(..., ge=1, le=365, description="Default TTL for memories in days")
    session_max_history: int = Field(
        ..., ge=1, le=200, description="Max messages in session history"
    )
    max_context_items: int = Field(
        default=6, ge=1, le=20, description="Max context items per query"
    )
    session_max_tokens: int = Field(
        default=4096, ge=512, le=128000, description="Session context token budget"
    )
    session_summary_enabled: bool = Field(
        default=True, description="Enable automatic session summarization"
    )
    session_summary_min_turns: int = Field(
        default=4, ge=2, le=20, description="Min turns before summarizing"
    )
    session_summary_max_items: int = Field(
        default=3, ge=1, le=10, description="Max summaries per session"
    )
    session_summary_max_messages: int = Field(
        default=100, ge=10, le=1000, description="Max messages per summary input"
    )
    session_summary_idle_minutes: int = Field(
        default=15, ge=5, le=120, description="Idle minutes before triggering summary"
    )
    session_summary_startup_backfill: bool = Field(
        default=False, description="Summarize unsummarized sessions at startup"
    )
    consolidation_enabled: bool = Field(default=False, description="Enable memory consolidation")
    consolidation_min_cluster: int = Field(
        default=3, ge=2, le=20, description="Min cluster size for consolidation"
    )
    semantic_cluster_enabled: bool = Field(default=False, description="Enable semantic clustering")
    knowledge_graph_enabled: bool = Field(
        default=False, description="Enable knowledge graph extraction"
    )
    profile_enabled: bool = Field(default=False, description="Enable user profile refresh")
    profile_refresh_interval: int = Field(
        default=50, ge=10, le=500, description="Profile refresh interval (turns)"
    )
    extraction_mode: str = Field(
        default="balanced",
        pattern="^(precise|balanced|aggressive)$",
        description="Fact extraction granularity",
    )
    entity_relations_limit: int = Field(
        default=50, ge=5, le=500, description="Max relations per entity"
    )
    global_memory_limit: int = Field(
        default=3, ge=0, le=20, description="Global memories per query"
    )
    skip_threshold: int = Field(
        default=3, ge=1, le=20, description="Min turns before skipping context"
    )


class SearchSettings(BaseModel):
    binding: str = Field(..., description="Search provider binding")
    api_key: str = Field(default="", description="API key (masked for display)")
    region: str = Field(..., description="Search region code")
    max_results: int = Field(..., ge=1, le=20, description="Maximum search results")
    timeout: int = Field(..., ge=1, le=60, description="Search timeout in seconds")
    web_search_timeout_s: float = Field(
        default=8.0, ge=1.0, le=60.0, description="Web search context timeout"
    )


class UploadSettings(BaseModel):
    max_size_mb: int = Field(..., ge=10, le=2000, description="Maximum upload size in MB")
    auto_summarize_files: bool = Field(default=True, description="Auto-summarize uploaded files")
    multimodal_captioning_enabled: bool = Field(
        default=True, description="Enable image captioning during upload"
    )
    ocr_dedup_enabled: bool = Field(default=True, description="Enable OCR deduplication")
    ocr_dedup_timeout_seconds: float = Field(
        default=8.0, ge=1.0, le=60.0, description="OCR dedup timeout"
    )
    video_keyframes_enabled: bool = Field(
        default=False, description="Enable video keyframe extraction"
    )


class ASRSettings(BaseModel):
    provider: Literal["assemblyai"] = Field(default="assemblyai", description="ASR provider")
    language: str = Field(default="en", description="Speech recognition language")
    assemblyai_api_key: str = Field(
        default="", description="AssemblyAI API key (env-only, masked for display)"
    )
    speech_model: str = Field(default="universal-3-pro", description="AssemblyAI speech model")
    speaker_labels: bool = Field(default=True, description="Enable speaker diarization")
    language_detection: bool = Field(default=True, description="Auto-detect language")
    poll_interval_seconds: int = Field(
        default=3, ge=1, le=30, description="Poll interval in seconds"
    )
    max_wait_seconds: int = Field(default=1800, ge=60, le=7200, description="Max wait in seconds")


class OCRSettings(BaseModel):
    provider: str = Field(
        default="marker",
        pattern="^(marker|mineru|paddleocr)$",
        description="OCR provider: marker|mineru|paddleocr",
    )
    language: str = Field(default="en", description="OCR language code")
    dpi: int = Field(default=300, ge=72, le=600, description="Scan DPI")
    marker_base_url: str = Field(default="", description="Marker API base URL")
    marker_api_key: str = Field(default="", description="Marker API key (masked)")
    marker_max_wait_seconds: int = Field(default=300, ge=10, le=1800)
    mineru_base_url: str = Field(default="", description="MinerU API base URL")
    mineru_api_key: str = Field(default="", description="MinerU API key (masked)")
    mineru_max_wait_seconds: int = Field(default=600, ge=10, le=3600)
    paddleocr_base_url: str = Field(default="", description="PaddleOCR base URL")
    paddleocr_api_key: str = Field(default="", description="PaddleOCR API key (masked)")
    http_timeout_seconds: float = Field(default=180.0, ge=5.0, le=600.0)
    poll_interval_seconds: float = Field(default=2.0, ge=0.5, le=30.0)


class VisionSettings(BaseModel):
    model: str = Field(default="", description="Vision model name (empty=disabled)")
    api_key: str = Field(default="", description="Vision API key (masked)")
    base_url: str = Field(default="", description="Vision API base URL")
    retry_max_attempts: int = Field(default=3, ge=1, le=10)
    retry_base_delay_seconds: float = Field(default=0.5, ge=0.1, le=10.0)
    retry_max_delay_seconds: float = Field(default=2.0, ge=1.0, le=60.0)
    caption_min_chars: int = Field(default=12, ge=1, le=500)
    ocr_min_chars: int = Field(default=6, ge=1, le=200)


class TTSSettings(BaseModel):
    binding: str = Field(default="", description="TTS provider binding (empty=disabled)")
    model: str = Field(default="", description="TTS model name")
    api_key: str = Field(default="", description="TTS API key (masked)")
    base_url: str = Field(default="", description="TTS API base URL")
    voice: str = Field(default="", description="TTS voice identifier")
    speed: float = Field(default=1.0, ge=0.25, le=4.0, json_schema_extra={"step": 0.1})


class ParserSettings(BaseModel):
    max_parse_pages: int = Field(default=1000, ge=1, le=10000)
    parse_timeout_seconds: int = Field(default=900, ge=30, le=3600)
    timeout_per_mb_seconds: int = Field(default=2, ge=1, le=60)
    timeout_max_seconds: int = Field(default=900, ge=30, le=3600)
    max_images_per_page: int = Field(default=20, ge=0, le=100)
    max_image_bytes: int = Field(default=8_388_608, ge=1024, le=67_108_864)
    doc_clean_repetition_min_pages: int = Field(default=3, ge=1, le=50)
    doc_clean_repetition_min_ratio: float = Field(default=0.6, ge=0.1, le=1.0)
    doc_clean_header_footer_max_lines: int = Field(default=2, ge=0, le=20)
    doc_clean_repetition_max_line_length: int = Field(default=120, ge=40, le=1000)


class RetentionSettings(BaseModel):
    chat_message_retention_days: int = Field(default=180, ge=1, le=3650)
    decay_state_retention_days: int = Field(default=365, ge=1, le=3650)


class ServerInfo(BaseModel):
    environment: str = Field(description="Runtime environment")
    host: str = Field(description="Bind address")
    port: int = Field(description="Listen port")
    cors_origins: str = Field(default="", description="CORS allowed origins")
    trusted_proxies: str = Field(default="")
    trusted_hosts: str = Field(default="")
    security_headers_enabled: bool = Field(default=True)
    security_hsts_max_age: int = Field(default=31536000)
    security_frame_options: str = Field(default="DENY")
    security_referrer_policy: str = Field(default="strict-origin-when-cross-origin")
    security_csp: str = Field(default="")


class SettingsActivationPolicy(BaseModel):
    """Machine-readable activation boundary for every backend Settings field."""

    hot: list[str]
    resettable: list[str]
    reindex_required: list[str]
    restart_required: list[str]


class SettingsResponse(BaseModel):
    llm: LLMSettings
    embedding: EmbeddingSettings
    rag: RAGSettings
    memory: MemorySettings
    search: SearchSettings
    upload: UploadSettings
    asr: ASRSettings
    ocr: OCRSettings
    vision: VisionSettings
    tts: TTSSettings
    parser: ParserSettings
    retention: RetentionSettings
    server: ServerInfo
    activation_policy: SettingsActivationPolicy


class SettingsUpdateRequest(BaseModel):
    llm: LLMSettings | None = None
    embedding: EmbeddingSettings | None = None
    rag: RAGSettings | None = None
    memory: MemorySettings | None = None
    search: SearchSettings | None = None
    upload: UploadSettings | None = None
    asr: ASRSettings | None = None
    ocr: OCRSettings | None = None
    vision: VisionSettings | None = None
    tts: TTSSettings | None = None
    parser: ParserSettings | None = None
    retention: RetentionSettings | None = None
