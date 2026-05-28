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
    reasoning_content : str | None
        Full reasoning / thinking text emitted by the model when
        reasoning mode is enabled (OpenAI o-series ``reasoning_content``,
        Anthropic ``thinking`` blocks, Gemini ``thoughts``, DeepSeek-R1
        ``reasoning_content``). ``None`` when the request did not enable
        reasoning or the provider returned no reasoning output. T1 just
        threads this field through the dataclass — adapters do not
        populate it yet; T2 wires per-provider parsing.
    """

    content: str
    model: str
    finish_reason: str = "stop"
    usage: ProviderUsage | None = None
    raw: dict = field(default_factory=dict)
    # Function-calling: populated by the provider parser when the model
    # invoked a tool. Each entry mirrors the OpenAI schema
    # ({"id", "type", "function": {"name", "arguments"}}). None when the
    # response is plain text — keeps the dataclass backward-compatible.
    tool_calls: list[dict] | None = None
    # Reasoning / thinking text. Populated by T2 adapter changes once
    # per-provider parsing lands; defaults to None so every existing
    # adapter (which does not set it) stays byte-identical.
    reasoning_content: str | None = None


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
    reasoning_delta : str | None
        Incremental reasoning / thinking text fragment. Populated only
        when the model emits a thinking-block delta (Anthropic
        ``thinking_delta``, OpenAI-compatible ``delta.reasoning_content``,
        Gemini ``thoughts`` parts). ``None`` for plain text / tool-call
        chunks. T1 just threads the field; T2 wires adapters + the SSE
        emitter to forward it.
    """

    delta: str = ""
    finish_reason: str | None = None
    model: str = ""
    usage: ProviderUsage | None = None
    # Tool-call delta fragments streamed by OpenAI when the model is
    # invoking a function. The first chunk carries
    # ``[{index, id, type, function:{name, arguments:""}}]`` and
    # subsequent chunks append to ``function.arguments``. ``None`` when
    # the chunk is plain text. A single chunk MAY carry both ``delta``
    # (text) and ``tool_calls``; the SSE emitter forwards each
    # independently.
    tool_calls: list[dict] | None = None
    # Reasoning / thinking delta. A single chunk MAY carry both
    # ``delta`` (final-answer text) and ``reasoning_delta`` (thought
    # text); T2's SSE emitter forwards each independently.
    reasoning_delta: str | None = None
