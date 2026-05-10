"""Web search service - supports multiple search providers"""

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from ..core.config import settings
from ..core.http_client import LoopBoundAsyncClient

logger = logging.getLogger(__name__)

_http_client = LoopBoundAsyncClient(
    lambda: httpx.AsyncClient(
        timeout=settings.SEARCH_TIMEOUT,
        limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
    )
)


def get_http_client() -> httpx.AsyncClient:
    """Get or create the shared loop-bound httpx AsyncClient."""
    return _http_client.get()


async def close_http_client() -> None:
    """Close the shared httpx client (call on shutdown)."""
    await _http_client.close()


@dataclass
class SearchResult:
    """Web search result item"""

    title: str
    url: str
    snippet: str
    source: str  # search provider name


async def search_duckduckgo(query: str, max_results: int = 5) -> list[SearchResult]:
    """Search using DuckDuckGo (free, no API key required)"""
    try:
        from duckduckgo_search import AsyncDDGS  # type: ignore[attr-defined]

        async with AsyncDDGS() as ddgs:
            results = []
            async for r in ddgs.text(
                query,
                region=settings.SEARCH_REGION,
                safesearch="moderate",
                max_results=max_results,
            ):
                results.append(
                    SearchResult(
                        title=r.get("title", ""),
                        url=r.get("href", ""),
                        snippet=r.get("body", ""),
                        source="duckduckgo",
                    )
                )
            return results
    except Exception as e:
        logger.warning("DuckDuckGo search failed: %s", e)
        return []


async def search_serpapi(query: str, max_results: int = 5) -> list[SearchResult]:
    """Search using SerpAPI (Google search results)"""
    api_key = settings.SEARCH_API_KEY.get_secret_value()
    if not api_key:
        raise ValueError("SerpAPI requires SEARCH_API_KEY")

    url = "https://serpapi.com/search"
    params = {
        "q": query,
        "api_key": api_key,
        "engine": "google",
        "num": max_results,
    }

    client = get_http_client()
    response = await client.get(url, params=params)
    response.raise_for_status()
    data = response.json()

    results = []
    for r in data.get("organic_results", [])[:max_results]:
        results.append(
            SearchResult(
                title=r.get("title", ""),
                url=r.get("link", ""),
                snippet=r.get("snippet", ""),
                source="serpapi",
            )
        )
    return results


async def search_tavily(query: str, max_results: int = 5) -> list[SearchResult]:
    """Search using Tavily (AI search engine)"""
    api_key = settings.SEARCH_API_KEY.get_secret_value()
    if not api_key:
        raise ValueError("Tavily requires SEARCH_API_KEY")

    url = "https://api.tavily.com/search"
    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": "basic",
        "include_answer": False,
        "max_results": max_results,
    }

    client = get_http_client()
    response = await client.post(url, json=payload)
    response.raise_for_status()
    data = response.json()

    results = []
    for r in data.get("results", []):
        results.append(
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("content", ""),
                source="tavily",
            )
        )
    return results


async def search_bing(query: str, max_results: int = 5) -> list[SearchResult]:
    """Search using Bing Search API"""
    api_key = settings.SEARCH_API_KEY.get_secret_value()
    if not api_key:
        raise ValueError("Bing Search requires SEARCH_API_KEY")

    url = "https://api.bing.microsoft.com/v7.0/search"
    headers = {"Ocp-Apim-Subscription-Key": api_key}
    params = {"q": query, "count": max_results, "textDecorations": False}

    client = get_http_client()
    response = await client.get(url, headers=headers, params=params)
    response.raise_for_status()
    data = response.json()

    results = []
    for r in data.get("webPages", {}).get("value", [])[:max_results]:
        results.append(
            SearchResult(
                title=r.get("name", ""),
                url=r.get("url", ""),
                snippet=r.get("snippet", ""),
                source="bing",
            )
        )
    return results


async def search_exa(query: str, max_results: int = 5) -> list[SearchResult]:
    """Search using Exa (semantic search)"""
    api_key = settings.SEARCH_API_KEY.get_secret_value()
    if not api_key:
        raise ValueError("Exa requires SEARCH_API_KEY")

    url = "https://api.exa.ai/search"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "query": query,
        "numResults": max_results,
        "contents": {"text": True},
    }

    client = get_http_client()
    response = await client.post(url, headers=headers, json=payload)
    response.raise_for_status()
    data = response.json()

    results = []
    for r in data.get("results", []):
        results.append(
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("text", ""),
                source="exa",
            )
        )
    return results


async def web_search(query: str, max_results: int | None = None) -> list[SearchResult]:
    """
    Perform web search using configured provider.

    Args:
        query: Search query
        max_results: Number of results (defaults to settings.SEARCH_MAX_RESULTS)

    Returns:
        List of search results
    """
    binding = (settings.SEARCH_BINDING or "duckduckgo").lower()
    max_results = max_results or settings.SEARCH_MAX_RESULTS

    # Map binding to search function
    search_functions: dict[str, Any] = {
        "duckduckgo": search_duckduckgo,
        "serpapi": search_serpapi,
        "tavily": search_tavily,
        "bing": search_bing,
        "exa": search_exa,
    }

    if binding not in search_functions:
        raise ValueError(
            f"Unsupported search binding: {binding}. "
            f"Supported: {', '.join(search_functions.keys())}"
        )

    try:
        results = await search_functions[binding](query, max_results)
        logger.info(
            "Web search [%s] for '%s' returned %d results",
            binding,
            query[:50],
            len(results),
        )
        return results
    except Exception as e:
        logger.error("Web search [%s] failed: %s", binding, e)
        return []


def format_search_results(results: list[SearchResult]) -> str:
    """Format search results into context string for LLM"""
    if not results:
        return ""

    _SAFE_PROTOCOLS = ("https://", "http://")
    parts = ["Web Search Results:"]
    idx = 0
    for r in results:
        url = r.url or ""
        if not url.lower().startswith(_SAFE_PROTOCOLS):
            continue
        idx += 1
        parts.append(f"\n[{idx}] {r.title}\nURL: {url}\n{r.snippet}")

    return "\n".join(parts)


async def search_with_fallback(query: str, max_results: int | None = None) -> list[SearchResult]:
    """
    Search with fallback to DuckDuckGo if configured provider fails.
    Useful when API key expires or rate limited.
    """
    binding = settings.SEARCH_BINDING.lower()
    max_results = max_results or settings.SEARCH_MAX_RESULTS

    # Try configured provider first
    if binding and binding != "duckduckgo":
        try:
            results = await web_search(query, max_results)
            if results:
                return results
            logger.warning("Primary search returned no results, trying fallback")
        except Exception as e:
            logger.warning("Primary search failed (%s), trying fallback: %s", binding, e)

    # Fallback to DuckDuckGo (free)
    return await search_duckduckgo(query, max_results)
