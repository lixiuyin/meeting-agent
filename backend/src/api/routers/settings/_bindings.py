"""Settings bindings endpoint — available provider listings."""

from typing import Any

from ._common import router


@router.get("/bindings", response_model=dict[str, Any])
async def get_available_bindings() -> dict[str, Any]:
    """Get available provider bindings for various components."""
    from ....services.llm import list_llm_providers

    return {
        "llm": list_llm_providers(),
        "embedding": [
            "openai",
            "azure_openai",
            "jina",
            "cohere",
            "huggingface",
            "google",
            "ollama",
            "lm_studio",
        ],
        "search": [
            "",
            "duckduckgo",
            "serpapi",
            "tavily",
            "bing",
            "exa",
        ],
        "reranker": [
            "",
            "cohere",
            "http",
            "bge",
        ],
        "tts": [
            "",
            "openai",
            "edge",
            "cohere",
            "local",
        ],
        "asr": [
            "assemblyai",
        ],
        "ocr": [
            "marker",
            "mineru",
            "paddleocr",
        ],
        "vision": [
            "",
            "openai",
            "azure_openai",
            "anthropic",
            "google",
        ],
    }
