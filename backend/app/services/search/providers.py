"""Search provider adapters — one per engine, all returning SearchResults.

Tavily and Brave are the launch providers (and the recommended primary /
first-fallback). SerpAPI, Google Custom Search, and Bing are included so
the full provider set the admin UI offers is callable. Each adapter
normalizes its engine's response into the shared SearchResult shape and
raises SearchProviderError on any failure so SearchService can fall
through to the next provider.
"""

from __future__ import annotations

import os
import time

import httpx

from app.services.search.base import (
    SearchProvider,
    SearchProviderError,
    SearchResult,
    SearchResults,
)

# One client config for all adapters. 10s is the per-provider ceiling
# the SearchService also enforces via asyncio.wait_for.
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class TavilySearchProvider(SearchProvider):
    """Tavily — purpose-built for AI agents. POST /search with the key in
    the body. https://docs.tavily.com/documentation/api-reference/search
    """

    provider_name = "tavily"
    BASE_URL = "https://api.tavily.com"

    async def search(self, query: str, max_results: int) -> SearchResults:
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{self.BASE_URL}/search",
                    json={
                        "api_key": self.api_key,
                        "query": query,
                        "max_results": max_results,
                        "search_depth": "basic",
                    },
                )
        except httpx.HTTPError as e:
            raise SearchProviderError(f"tavily transport: {e}") from e
        if resp.status_code != 200:
            raise SearchProviderError(f"tavily HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        results = [
            SearchResult(
                title=r.get("title", "") or "",
                url=r.get("url", "") or "",
                snippet=r.get("content", "") or "",
                published_date=r.get("published_date"),
            )
            for r in (data.get("results") or [])
            if r.get("url")
        ]
        return SearchResults(
            query=query,
            provider=self.provider_name,
            results=results,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )


class BraveSearchProvider(SearchProvider):
    """Brave Search — GET /web/search with the key in a header."""

    provider_name = "brave"
    BASE_URL = "https://api.search.brave.com/res/v1"

    async def search(self, query: str, max_results: int) -> SearchResults:
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    f"{self.BASE_URL}/web/search",
                    headers={
                        "X-Subscription-Token": self.api_key,
                        "Accept": "application/json",
                    },
                    params={"q": query, "count": max_results},
                )
        except httpx.HTTPError as e:
            raise SearchProviderError(f"brave transport: {e}") from e
        if resp.status_code != 200:
            raise SearchProviderError(f"brave HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        web = (data.get("web") or {}).get("results") or []
        results = [
            SearchResult(
                title=r.get("title", "") or "",
                url=r.get("url", "") or "",
                snippet=r.get("description", "") or "",
                published_date=r.get("age"),
            )
            for r in web
            if r.get("url")
        ]
        return SearchResults(
            query=query,
            provider=self.provider_name,
            results=results,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )


class SerpAPISearchProvider(SearchProvider):
    """SerpAPI (Google engine) — GET /search with key in params."""

    provider_name = "serpapi"
    BASE_URL = "https://serpapi.com"

    async def search(self, query: str, max_results: int) -> SearchResults:
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    f"{self.BASE_URL}/search",
                    params={
                        "q": query,
                        "num": max_results,
                        "api_key": self.api_key,
                        "engine": "google",
                    },
                )
        except httpx.HTTPError as e:
            raise SearchProviderError(f"serpapi transport: {e}") from e
        if resp.status_code != 200:
            raise SearchProviderError(f"serpapi HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        organic = data.get("organic_results") or []
        results = [
            SearchResult(
                title=r.get("title", "") or "",
                url=r.get("link", "") or "",
                snippet=r.get("snippet", "") or "",
                published_date=r.get("date"),
            )
            for r in organic[:max_results]
            if r.get("link")
        ]
        return SearchResults(
            query=query,
            provider=self.provider_name,
            results=results,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )


class GoogleSearchProvider(SearchProvider):
    """Google Custom Search — needs both an API key AND a Custom Search
    Engine id. Stored together as ``API_KEY:CX_ID`` and split here."""

    provider_name = "google"
    BASE_URL = "https://www.googleapis.com/customsearch/v1"

    async def search(self, query: str, max_results: int) -> SearchResults:
        if ":" not in self.api_key:
            raise SearchProviderError(
                "google key must be stored as 'API_KEY:CX_ID'"
            )
        key, cx = self.api_key.split(":", 1)
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    self.BASE_URL,
                    params={
                        "q": query,
                        # Google caps num at 10.
                        "num": min(max_results, 10),
                        "key": key,
                        "cx": cx,
                    },
                )
        except httpx.HTTPError as e:
            raise SearchProviderError(f"google transport: {e}") from e
        if resp.status_code != 200:
            raise SearchProviderError(f"google HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        items = data.get("items") or []
        results = [
            SearchResult(
                title=r.get("title", "") or "",
                url=r.get("link", "") or "",
                snippet=r.get("snippet", "") or "",
            )
            for r in items
            if r.get("link")
        ]
        return SearchResults(
            query=query,
            provider=self.provider_name,
            results=results,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )


class BingSearchProvider(SearchProvider):
    """Bing Web Search — GET /search with key in a header."""

    provider_name = "bing"
    BASE_URL = "https://api.bing.microsoft.com/v7.0"

    async def search(self, query: str, max_results: int) -> SearchResults:
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    f"{self.BASE_URL}/search",
                    headers={"Ocp-Apim-Subscription-Key": self.api_key},
                    params={"q": query, "count": max_results},
                )
        except httpx.HTTPError as e:
            raise SearchProviderError(f"bing transport: {e}") from e
        if resp.status_code != 200:
            raise SearchProviderError(f"bing HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        web = (data.get("webPages") or {}).get("value") or []
        results = [
            SearchResult(
                title=r.get("name", "") or "",
                url=r.get("url", "") or "",
                snippet=r.get("snippet", "") or "",
                published_date=r.get("dateLastCrawled"),
            )
            for r in web
            if r.get("url")
        ]
        return SearchResults(
            query=query,
            provider=self.provider_name,
            results=results,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )


class MockSearchProvider(SearchProvider):
    """FIX51 Part 2.2 — dev-only engine against the local mock server.

    Exists so the WHOLE search path (tenant gate → chain → normalize →
    credit charge → web_search tool → citations) can be proven live
    with no real key and no spend — the same lever pattern as the mock
    chat provider's ``__echo64__`` / ``__tool__``. Registered only
    outside production (see below), so the admin dropdown never offers
    it to a real deployment.
    """

    provider_name = "mock"
    BASE_URL = os.environ.get("MOCK_SEARCH_BASE_URL", "http://mock-provider:9090")

    async def search(self, query: str, max_results: int) -> SearchResults:
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{self.BASE_URL}/search",
                    json={"query": query, "max_results": max_results},
                )
        except httpx.HTTPError as e:
            raise SearchProviderError(f"mock transport: {e}") from e
        if resp.status_code != 200:
            raise SearchProviderError(f"mock HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        results = [
            SearchResult(
                title=r.get("title", "") or "",
                url=r.get("url", "") or "",
                snippet=r.get("content", "") or "",
                published_date=r.get("published_date"),
            )
            for r in (data.get("results") or [])
            if r.get("url")
        ]
        return SearchResults(
            query=query,
            provider=self.provider_name,
            results=results,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )


# Registry — provider name → adapter class. The admin "add provider"
# dropdown and SearchService both validate against these keys.
PROVIDER_ADAPTERS: dict[str, type[SearchProvider]] = {
    "tavily": TavilySearchProvider,
    "brave": BraveSearchProvider,
    "serpapi": SerpAPISearchProvider,
    "google": GoogleSearchProvider,
    "bing": BingSearchProvider,
}

# The mock engine is a dev/test lever, never a production offer.
if os.environ.get("APP_ENV", "development") != "production":
    PROVIDER_ADAPTERS["mock"] = MockSearchProvider

SUPPORTED_PROVIDERS = tuple(PROVIDER_ADAPTERS.keys())


def get_adapter(provider: str, api_key: str) -> SearchProvider:
    """Build an adapter for ``provider`` with the decrypted ``api_key``."""
    cls = PROVIDER_ADAPTERS.get(provider)
    if cls is None:
        raise SearchProviderError(f"unknown search provider: {provider}")
    return cls(api_key)
