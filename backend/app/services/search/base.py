"""Search provider interface + normalized result types.

Every provider adapter returns the same ``SearchResults`` shape so the
SearchService fallback chain and the chat-edge tool are provider-
agnostic — the model never sees which engine answered.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from urllib.parse import urlparse


def domain_of(url: str) -> str:
    """Extract a bare hostname (no www.) for citation chips/favicons."""
    try:
        host = urlparse(url).hostname or ""
    except (ValueError, TypeError):
        return ""
    return host[4:] if host.startswith("www.") else host


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    domain: str = ""
    published_date: str | None = None

    def __post_init__(self) -> None:
        if not self.domain:
            self.domain = domain_of(self.url)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "domain": self.domain,
            "published_date": self.published_date,
        }


@dataclass
class SearchResults:
    query: str
    provider: str
    results: list[SearchResult] = field(default_factory=list)
    latency_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "provider": self.provider,
            "latency_ms": self.latency_ms,
            "results": [r.to_dict() for r in self.results],
        }


class SearchProviderError(Exception):
    """A single provider failed — the service moves to the next one."""


class SearchNotConfiguredError(Exception):
    """No enabled search providers exist for the tenant."""


class SearchUnavailableError(Exception):
    """Every configured provider failed."""


class SearchProvider(ABC):
    """One web-search engine. ``provider_name`` matches the DB ``provider``
    column ('tavily' / 'brave' / ...). ``api_key`` is the decrypted key.
    """

    provider_name: str = ""

    def __init__(self, api_key: str):
        self.api_key = api_key

    @abstractmethod
    async def search(self, query: str, max_results: int) -> SearchResults:
        """Execute a query, return normalized results. Raise
        SearchProviderError on any failure so the chain can fall through."""
        raise NotImplementedError

    async def health_check(self) -> bool:
        """Lightweight liveness probe — a 1-result search. True on success.
        Default reuses ``search``; providers may override for a cheaper
        probe."""
        await self.search("current weather", max_results=1)
        return True
