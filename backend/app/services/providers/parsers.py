"""Shared response parsers for provider adapters.

OpenAI and OpenRouter both return the same response format.
This module eliminates duplication and ensures consistent normalization.
"""

from __future__ import annotations

import json

from app.domain.providers import ProviderResponse, ProviderUsage


def parse_openai_response(data: dict) -> ProviderResponse:
    """Parse an OpenAI-compatible chat completion response.

    Works for both OpenAI and OpenRouter responses, as well as the
    DeepSeek-R1 / Groq R1-distill bodies which share the same wire
    shape and add ``message.reasoning_content``.
    """
    choice = data["choices"][0]
    message = choice["message"]

    usage = None
    if "usage" in data:
        u = data["usage"]
        # OpenAI o-series surfaces reasoning token count under
        # ``completion_tokens_details.reasoning_tokens``. Absent on
        # non-reasoning models / non-OpenAI providers → 0.
        details = u.get("completion_tokens_details") or {}
        reasoning_tok = (
            int(details.get("reasoning_tokens", 0) or 0)
            if isinstance(details, dict) else 0
        )
        # OpenAI reports the prompt-cache hit under
        # ``usage.prompt_tokens_details.cached_tokens`` (free to read,
        # cached input bills at a reduced rate). Absent on non-OpenAI
        # providers / older responses → 0.
        prompt_details = u.get("prompt_tokens_details") or {}
        cached_tok = (
            int(prompt_details.get("cached_tokens", 0) or 0)
            if isinstance(prompt_details, dict) else 0
        )
        usage = ProviderUsage(
            prompt_tokens=u.get("prompt_tokens", 0),
            completion_tokens=u.get("completion_tokens", 0),
            total_tokens=u.get("total_tokens", 0),
            reasoning_tokens=reasoning_tok,
            cached_prompt_tokens=cached_tok,
        )

    # Reasoning text — only DeepSeek-R1 / Groq R1-distill / OpenRouter
    # routed to reasoning models surface this on the non-streaming
    # response (OpenAI o-series via /chat/completions doesn't return
    # reasoning text, only the token count). ``None`` when absent so
    # the response stays byte-identical for plain OpenAI calls.
    reasoning_raw = message.get("reasoning_content")
    reasoning_content = (
        reasoning_raw if isinstance(reasoning_raw, str) and reasoning_raw else None
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
        reasoning_content=reasoning_content,
    )


def parse_anthropic_response(data: dict) -> ProviderResponse:
    """Parse an Anthropic Messages API response.

    Normalises function-calling output: Anthropic returns ``tool_use``
    content blocks alongside ``text`` blocks; this parser surfaces
    them as OpenAI-format ``tool_calls`` on the ProviderResponse so
    clients see the same shape regardless of which provider answered.

    Extended thinking (T2): when the request enabled the ``thinking``
    parameter, Anthropic also returns ``thinking`` content blocks
    before the visible text. The parser concatenates the thinking
    text into ``reasoning_content``; ``redacted_thinking`` blocks are
    deliberately dropped (they're opaque to the client and have no
    text payload).
    """
    content_blocks = data.get("content", [])
    text_parts = [
        block.get("text", "")
        for block in content_blocks
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    content = "".join(text_parts)

    # Extended thinking — text from visible thinking blocks. ``None``
    # when the response carries no thinking (the request didn't
    # enable it, or the model decided not to think) so the response
    # field is dropped from the wire by ``response_model_exclude_none``.
    thinking_parts = [
        block.get("thinking", "")
        for block in content_blocks
        if isinstance(block, dict) and block.get("type") == "thinking"
    ]
    reasoning_content = "".join(thinking_parts) or None

    # T5 — preserve the thinking-block signature so a tool-loop client can
    # replay the thinking verbatim on the next turn. Use the first signed
    # thinking block's signature (Anthropic emits one per thinking block;
    # tool-loop turns carry a single block). ``None`` when absent.
    reasoning_signature = next(
        (
            block.get("signature")
            for block in content_blocks
            if isinstance(block, dict)
            and block.get("type") == "thinking"
            and block.get("signature")
        ),
        None,
    )

    # Convert each ``tool_use`` block to the OpenAI tool_call shape.
    # Anthropic exposes arguments as a JSON object on ``input``; OpenAI
    # carries them as a JSON *string* on ``function.arguments`` — we
    # re-serialise so the orchestrator → client wire shape is the
    # same as a native OpenAI response.
    tool_calls: list[dict] | None = None
    tool_use_blocks = [
        block for block in content_blocks
        if isinstance(block, dict) and block.get("type") == "tool_use"
    ]
    if tool_use_blocks:
        tool_calls = [
            {
                "id": block.get("id", ""),
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    "arguments": json.dumps(
                        block.get("input") or {}, ensure_ascii=False
                    ),
                },
            }
            for block in tool_use_blocks
        ]

    stop_reason = data.get("stop_reason", "end_turn")
    _finish_map = {
        "end_turn": "stop",
        "max_tokens": "length",
        "stop_sequence": "stop",
        # Anthropic's signal that the model invoked a tool. Mapping to
        # OpenAI's "tool_calls" keeps the client-visible vocabulary
        # consistent across providers.
        "tool_use": "tool_calls",
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
        tool_calls=tool_calls,
        reasoning_content=reasoning_content,
        reasoning_signature=reasoning_signature,
    )
