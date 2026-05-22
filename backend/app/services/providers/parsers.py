"""Shared response parsers for provider adapters.

OpenAI and OpenRouter both return the same response format.
This module eliminates duplication and ensures consistent normalization.
"""

from __future__ import annotations

from app.domain.providers import ProviderResponse, ProviderUsage


def parse_openai_response(data: dict) -> ProviderResponse:
    """Parse an OpenAI-compatible chat completion response.

    Works for both OpenAI and OpenRouter responses.
    """
    choice = data["choices"][0]
    message = choice["message"]

    usage = None
    if "usage" in data:
        u = data["usage"]
        usage = ProviderUsage(
            prompt_tokens=u.get("prompt_tokens", 0),
            completion_tokens=u.get("completion_tokens", 0),
            total_tokens=u.get("total_tokens", 0),
        )

    return ProviderResponse(
        content=message.get("content", ""),
        model=data.get("model", ""),
        finish_reason=choice.get("finish_reason", "stop"),
        usage=usage,
        raw=data,
        # OpenAI returns tool_calls on the assistant message when the
        # model invoked a function. ``None`` when absent so we stay
        # backward-compat with plain-text responses; the downstream
        # serialiser uses ``response_model_exclude_none`` to drop it
        # from the wire when unset.
        tool_calls=message.get("tool_calls"),
    )


def parse_anthropic_response(data: dict) -> ProviderResponse:
    """Parse an Anthropic Messages API response."""
    content_blocks = data.get("content", [])
    text_parts = [
        block.get("text", "")
        for block in content_blocks
        if block.get("type") == "text"
    ]
    content = "".join(text_parts)

    stop_reason = data.get("stop_reason", "end_turn")
    _finish_map = {
        "end_turn": "stop",
        "max_tokens": "length",
        "stop_sequence": "stop",
    }
    finish_reason = _finish_map.get(stop_reason, stop_reason)

    usage = None
    if "usage" in data:
        u = data["usage"]
        input_tokens = u.get("input_tokens", 0)
        output_tokens = u.get("output_tokens", 0)
        usage = ProviderUsage(
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        )

    return ProviderResponse(
        content=content,
        model=data.get("model", ""),
        finish_reason=finish_reason,
        usage=usage,
        raw=data,
    )
