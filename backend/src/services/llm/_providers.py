"""LLM provider creators and registry."""

import logging
import threading
from collections.abc import Callable
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from ...core.config import settings

logger = logging.getLogger(__name__)

# Thread-safe LLM singleton
_llm: BaseChatModel | None = None
_lock = threading.Lock()


def _get_effective_binding() -> str:
    """Get the effective LLM binding."""
    if settings.LLM_BINDING:
        return settings.LLM_BINDING.lower()
    return "openai"


def _create_openai_llm(model_name: str | None = None) -> BaseChatModel:
    """Create OpenAI LLM instance."""
    from langchain_openai import ChatOpenAI

    model = model_name or settings.LLM_MODEL
    kwargs: dict[str, Any] = {
        "model": model,
        "temperature": settings.LLM_TEMPERATURE,
        "max_tokens": settings.LLM_MAX_TOKENS,
    }
    llm_key = settings.LLM_API_KEY.get_secret_value()
    if llm_key:
        kwargs["api_key"] = llm_key
    base_url = settings.LLM_BASE_URL or "https://api.openai.com/v1"
    kwargs["base_url"] = base_url
    # Suppress reasoning tokens for o-series models so they don't leak into
    # the streaming content path.  Non-o-series models ignore this parameter.
    model_lower = model.lower()
    if model_lower.startswith(("o1", "o3", "o4")):
        kwargs["extra_body"] = {"reasoning": {"effort": "high"}}
    return ChatOpenAI(**kwargs)  # type: ignore[arg-type]


def _create_azure_openai_llm(model_name: str | None = None) -> BaseChatModel:
    """Create Azure OpenAI LLM instance."""
    from langchain_openai import AzureChatOpenAI

    kwargs: dict[str, Any] = {
        "model": model_name or settings.LLM_MODEL,
        "temperature": settings.LLM_TEMPERATURE,
        "max_tokens": settings.LLM_MAX_TOKENS,
    }
    llm_key = settings.LLM_API_KEY.get_secret_value()
    if llm_key:
        kwargs["api_key"] = llm_key
    if settings.LLM_BASE_URL:
        kwargs["azure_endpoint"] = settings.LLM_BASE_URL

    return AzureChatOpenAI(**kwargs)  # type: ignore[arg-type]


def _create_anthropic_llm(model_name: str | None = None) -> BaseChatModel:
    """Create Anthropic LLM instance."""
    from langchain_anthropic import ChatAnthropic

    kwargs: dict[str, Any] = {
        "model": model_name or settings.LLM_MODEL,
        "temperature": settings.LLM_TEMPERATURE,
        "max_tokens": settings.LLM_MAX_TOKENS,
    }
    llm_key = settings.LLM_API_KEY.get_secret_value()
    if llm_key:
        kwargs["api_key"] = llm_key
    return ChatAnthropic(**kwargs)  # type: ignore[arg-type]


def _create_openai_compatible_llm(
    default_base_url: str | None = None,
    api_key_required: bool = True,
    model_name: str | None = None,
) -> BaseChatModel:
    """Create OpenAI-compatible LLM instance (for DeepSeek, OpenRouter, Groq, etc.)."""
    from langchain_openai import ChatOpenAI

    kwargs: dict[str, Any] = {
        "model": model_name or settings.LLM_MODEL,
        "temperature": settings.LLM_TEMPERATURE,
        "max_tokens": settings.LLM_MAX_TOKENS,
    }

    llm_key = settings.LLM_API_KEY.get_secret_value()
    if api_key_required or llm_key:
        kwargs["api_key"] = llm_key or "not-needed"

    base_url = settings.LLM_BASE_URL or settings.LLM_HOST or default_base_url
    if base_url:
        kwargs["base_url"] = base_url

    return ChatOpenAI(**kwargs)  # type: ignore[arg-type]


def _create_ollama_llm(model_name: str | None = None) -> BaseChatModel:
    """Create Ollama local LLM instance."""
    from langchain_ollama import ChatOllama

    base_url = settings.LLM_HOST or settings.LLM_BASE_URL or "http://localhost:11434"

    return ChatOllama(
        model=model_name or settings.LLM_MODEL,
        base_url=base_url,
        temperature=settings.LLM_TEMPERATURE,
    )


def _create_llama_cpp_llm(model_name: str | None = None) -> BaseChatModel:
    """Create LlamaCPP local LLM instance."""
    from langchain_community.chat_models import ChatLlamaCpp  # type: ignore[import-not-found]

    model_path = model_name or settings.LLM_MODEL

    return ChatLlamaCpp(
        model_path=model_path,
        temperature=settings.LLM_TEMPERATURE,
        max_tokens=settings.LLM_MAX_TOKENS,
    )


# ---------------------------------------------------------------------------
# Provider Registry
# ---------------------------------------------------------------------------

_LLM_CREATORS: dict[str, Callable[..., BaseChatModel]] = {}


def register_llm_provider(name: str) -> Callable:
    """Decorator to register an LLM provider creator function."""

    def decorator(fn: Callable[..., BaseChatModel]) -> Callable[..., BaseChatModel]:
        _LLM_CREATORS[name] = fn
        return fn

    return decorator


def list_llm_providers() -> list[str]:
    """Return all registered provider names."""
    return sorted(_LLM_CREATORS.keys())


# Register built-in providers
register_llm_provider("openai")(_create_openai_llm)
register_llm_provider("azure_openai")(_create_azure_openai_llm)
register_llm_provider("anthropic")(_create_anthropic_llm)
register_llm_provider("ollama")(_create_ollama_llm)
register_llm_provider("llama_cpp")(_create_llama_cpp_llm)

# Data-driven registration for OpenAI-compatible providers
_OPENAI_COMPATIBLE_PROVIDERS: dict[str, tuple[str | None, bool]] = {
    "deepseek": ("https://api.deepseek.com/v1", True),
    "openrouter": ("https://openrouter.ai/api/v1", True),
    "groq": ("https://api.groq.com/openai/v1", True),
    "together": ("https://api.together.xyz/v1", True),
    "mistral": ("https://api.mistral.ai/v1", True),
    "lm_studio": ("http://localhost:1234/v1", False),
    "vllm": ("http://localhost:8000/v1", False),
}

for _provider_name, (_url, _needs_key) in _OPENAI_COMPATIBLE_PROVIDERS.items():
    register_llm_provider(_provider_name)(
        lambda url=_url, needs_key=_needs_key, model_name=None: _create_openai_compatible_llm(
            url,
            needs_key,
            model_name=model_name,
        )
    )


def get_llm() -> BaseChatModel:
    """Get or create the singleton LLM instance (thread-safe)."""
    global _llm
    if _llm is None:
        with _lock:
            if _llm is None:  # double-check after acquiring lock
                binding = _get_effective_binding()

                if binding not in _LLM_CREATORS:
                    raise ValueError(
                        f"Unsupported LLM binding: {binding}. "
                        f"Supported: {', '.join(_LLM_CREATORS.keys())}"
                    )

                _llm = _LLM_CREATORS[binding]()
                logger.info("Initialized %s LLM with model %s", binding, settings.LLM_MODEL)
    return _llm


def reset_llm() -> None:
    """Reset the LLM singleton so the next call to get_llm() creates a fresh instance."""
    global _llm
    with _lock:
        _llm = None
    logger.info("LLM singleton reset")


# Thread-safe extraction LLM singleton (separate model from main LLM)
_extraction_llm: BaseChatModel | None = None
_extraction_llm_lock = threading.Lock()


def get_extraction_llm() -> BaseChatModel:
    """Get LLM for fact/entity extraction tasks.

    Uses MEMORY_EXTRACTION_MODEL when configured, otherwise falls back to the
    main LLM singleton. This allows using a cheaper model for background
    extraction tasks.
    """
    model_name = settings.MEMORY_EXTRACTION_MODEL
    if not model_name:
        return get_llm()

    global _extraction_llm
    if _extraction_llm is None:
        with _extraction_llm_lock:
            if _extraction_llm is None:
                binding = _get_effective_binding()
                if binding not in _LLM_CREATORS:
                    logger.warning(
                        "Unsupported binding '%s' for extraction LLM, falling back to main LLM",
                        binding,
                    )
                    return get_llm()
                # Pass model_name explicitly — no settings mutation needed.
                _extraction_llm = _LLM_CREATORS[binding](model_name=model_name)
                logger.info(
                    "Initialized extraction LLM: binding=%s, model=%s",
                    binding,
                    model_name,
                )
    return _extraction_llm


def reset_extraction_llm() -> None:
    """Reset the extraction LLM singleton."""
    global _extraction_llm
    with _extraction_llm_lock:
        _extraction_llm = None
    logger.info("Extraction LLM singleton reset")


# ---------------------------------------------------------------------------
# Vision Capability Detection
# ---------------------------------------------------------------------------

# Known vision-capable model name prefixes/substrings (lowercase).
# Covers GPT-4o, GPT-4 Turbo, GPT-4 Vision, Claude 3/4, Gemini, Qwen-VL,
# LLaVA, InternVL, Pixtral, and common multimodal variants.
_VISION_MODEL_PATTERNS: tuple[str, ...] = (
    "gpt-4o",
    "gpt-4-turbo",
    "gpt-4-vision",
    "gpt-4-v",
    "gpt-4.1",
    "o1",
    "o3",
    "o4",
    "claude-3",
    "claude-4",
    "gemini",
    "qwen-vl",
    "qwen2-vl",
    "qwen2.5-vl",
    "llava",
    "internvl",
    "pixtral",
    "cogvlm",
    "cogagent",
    "glm-4v",
    "minicpm-v",
    "phi-3.5-vision",
    "phi-4-vision",
    "molmo",
    "idefics",
    "aya-vision",
    "qwen3.5",
    "qwen3.6",
)

# Providers whose models generally support vision via OpenAI-compatible API.
_OPENAI_VISION_PROVIDERS: frozenset[str] = frozenset({"openai", "azure_openai"})


def supports_vision() -> bool:
    """Check if the current LLM binding + model supports vision (image input).

    Resolution order:
    1. Explicit ``LLM_SUPPORTS_VISION`` setting ("true"/"false")
    2. If ``LLM_SUPPORTS_VISION`` is "auto" and ``MULTIMODAL_CAPTIONING_ENABLED`` is true
    3. Auto-detection based on provider + model name
    """
    setting = settings.LLM_SUPPORTS_VISION.lower().strip()
    if setting == "true":
        return True
    if setting == "false":
        return False

    # If captioning worked, the endpoint must support vision
    if setting == "auto" and getattr(settings, "MULTIMODAL_CAPTIONING_ENABLED", False):
        return True

    # Auto-detect
    binding = _get_effective_binding()
    model = settings.LLM_MODEL.lower()

    # Check model name patterns
    for pattern in _VISION_MODEL_PATTERNS:
        if pattern in model:
            return True

    # Anthropic's Claude 3+ models all support vision
    if binding == "anthropic":
        return True

    # OpenAI and Azure OpenAI: assume vision for GPT-4+ and o-series
    return binding in _OPENAI_VISION_PROVIDERS and any(
        prefix in model for prefix in ("gpt-4", "o1", "o3", "o4")
    )
