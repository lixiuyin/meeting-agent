"""LLM service - LangChain wrapper with multi-provider support."""

from ._cache import _get_llm_cache, cached_invoke, cached_retry_invoke, retry_invoke
from ._embeddings_adapter import embed_documents_batched
from ._parsing import parse_llm_json
from ._prompts import (
    get_combined_extraction_prompt,
    get_contradiction_resolution_prompt,
    get_entity_extraction_prompt,
    get_fact_extraction_prompt,
    get_memory_consolidation_prompt,
    get_rag_prompt,
    get_session_summary_prompt,
    get_skill_prompt,
)
from ._providers import (
    get_extraction_llm,
    get_llm,
    list_llm_providers,
    register_llm_provider,
    reset_extraction_llm,
    reset_llm,
    supports_vision,
)
from ._telemetry import track_llm_call

__all__ = [
    "_get_llm_cache",
    "cached_invoke",
    "cached_retry_invoke",
    "embed_documents_batched",
    "get_combined_extraction_prompt",
    "get_contradiction_resolution_prompt",
    "get_entity_extraction_prompt",
    "get_extraction_llm",
    "get_fact_extraction_prompt",
    "get_llm",
    "get_memory_consolidation_prompt",
    "get_rag_prompt",
    "get_session_summary_prompt",
    "get_skill_prompt",
    "list_llm_providers",
    "parse_llm_json",
    "register_llm_provider",
    "reset_extraction_llm",
    "reset_llm",
    "retry_invoke",
    "supports_vision",
    "track_llm_call",
]
