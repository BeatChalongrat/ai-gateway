"""Canonical provider domain contracts.

These types define the boundary between provider adapters and the rest of
the gateway.  Every adapter normalizes its upstream response into a
ProviderResponse before returning.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderUsage:
    """Token usage as reported by the upstream provider."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class ProviderResponse:
    """Canonical response from any provider adapter.

    Attributes
    ----------
    content : str
        Assistant message text.
    model : str
        Model name as reported by the provider.
    finish_reason : str
        Why generation stopped — "stop", "length", "content_filter", etc.
    usage : ProviderUsage | None
        Token counts from the provider.  None when the provider doesn't
        report usage (e.g. some OpenRouter models).
    raw : dict
        Full raw provider JSON response for debugging / audit.
    """

    content: str
    model: str
    finish_reason: str = "stop"
    usage: ProviderUsage | None = None
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderStreamChunk:
    """A single chunk from a streaming provider response.

    Attributes
    ----------
    delta : str
        Incremental text content (may be empty for metadata-only chunks).
    finish_reason : str | None
        Set on the final chunk (e.g. "stop", "length"). None for intermediate chunks.
    model : str
        Model name (usually set on the first chunk, empty on subsequent).
    usage : ProviderUsage | None
        Token counts. Only set on the trailing usage chunk emitted when the
        adapter requests `stream_options.include_usage` from the upstream
        provider. None for intermediate content chunks.
    """

    delta: str = ""
    finish_reason: str | None = None
    model: str = ""
    usage: ProviderUsage | None = None
