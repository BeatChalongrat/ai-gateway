"""OpenAI Chat Completions adapter.

Calls the OpenAI API via httpx and normalizes the response into the
canonical ProviderResponse.  Supports both blocking and streaming modes.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, AsyncIterator

from app.core.config import settings
from app.domain.providers import ProviderResponse, ProviderStreamChunk, ProviderUsage
from app.services.providers.base import BaseProviderAdapter
from app.services.providers.base_urls import resolve_base_url
from app.services.providers.http_client import create_provider_client
from app.services.providers.error_handler import call_with_retry
from app.services.providers.parsers import parse_openai_response

if TYPE_CHECKING:
    from app.schemas.common import Message

OPENAI_BASE_URL = "https://api.openai.com/v1"

# Public reasoning mode → OpenAI ``reasoning_effort`` value.
# ``auto`` resolves to ``medium`` (same posture the audit recommended).
# ``off`` is filtered out before this table is consulted.
_REASONING_EFFORT_MAP: dict[str, str] = {
    "auto": "medium",
    "low": "low",
    "medium": "medium",
    "high": "high",
}


class OpenAIAdapter(BaseProviderAdapter):
    provider_name = "openai"
    supports_streaming = True

    @staticmethod
    def _serialize_message(msg) -> dict:
        """Build the OpenAI wire-format dict for a single Message.

        Includes content / tool_calls / tool_call_id / name only when
        set — OpenAI rejects e.g. ``"name": null`` on a tool result,
        and an assistant message with tool_calls is allowed to omit
        content entirely.
        """
        out: dict = {"role": msg.role}
        if msg.content is not None:
            out["content"] = msg.content
        if msg.tool_calls is not None:
            out["tool_calls"] = msg.tool_calls
        if msg.tool_call_id is not None:
            out["tool_call_id"] = msg.tool_call_id
        if msg.name is not None:
            out["name"] = msg.name
        return out

    def _build_body(
        self, payload, messages, *, stream: bool = False, resolved_model: str | None = None
    ) -> dict:
        model = resolved_model or payload.model
        body: dict = {
            "model": model,
            "messages": [self._serialize_message(m) for m in messages],
        }
        if stream:
            body["stream"] = True
            # Ask OpenAI to emit a final usage chunk so we can record
            # prompt/completion/total tokens for streaming requests.
            body["stream_options"] = {"include_usage": True}

        for param in ("temperature", "max_tokens", "top_p"):
            val = getattr(payload, param, None) or payload.metadata.get(param)
            if val is not None:
                body[param] = val

        # Function-calling passthrough. Forward only when set so a
        # plain chat request doesn't gain empty tool fields.
        tools = getattr(payload, "tools", None)
        if tools is not None:
            body["tools"] = tools
        tool_choice = getattr(payload, "tool_choice", None)
        if tool_choice is not None:
            body["tool_choice"] = tool_choice

        # Reasoning (T2) — o-series. Gated on both ``payload.reasoning``
        # being non-off AND the resolved model being flagged
        # ``supports_reasoning=True`` (migration 045). When both are
        # true we:
        #   1. Add ``reasoning_effort`` (low / medium / high).
        #   2. Rename ``max_tokens`` → ``max_completion_tokens`` —
        #      o-series rejects ``max_tokens`` because the completion
        #      budget must cover BOTH reasoning + visible answer.
        #   3. Drop ``temperature`` and ``top_p`` — o-series rejects
        #      both (they're fixed at 1 and ignore sampling knobs).
        # If either gate fails the request flows through unchanged so
        # gpt-4o-mini etc. continue to behave identically. Reasoning
        # summary text (``show_summary=true``) requires the Responses
        # API; on /chat/completions we only get the count back via
        # ``usage.completion_tokens_details.reasoning_tokens``.
        reasoning_cfg = getattr(payload, "reasoning", None)
        if reasoning_cfg is not None and reasoning_cfg.mode != "off":
            from app.services.providers.model_registry import get_candidate
            candidate = get_candidate(model)
            if candidate is not None and candidate.supports_reasoning:
                body["reasoning_effort"] = _REASONING_EFFORT_MAP.get(
                    reasoning_cfg.mode, _REASONING_EFFORT_MAP["auto"]
                )
                if "max_tokens" in body:
                    body["max_completion_tokens"] = body.pop("max_tokens")
                body.pop("temperature", None)
                body.pop("top_p", None)

        return body

    async def chat(
        self, payload, messages: list[Message], *, resolved_model: str | None = None, api_key: str | None = None
    ) -> ProviderResponse:
        body = self._build_body(payload, messages, resolved_model=resolved_model)
        key = api_key or settings.openai_api_key

        client = create_provider_client(
            base_url=resolve_base_url(self.provider_name, OPENAI_BASE_URL),
            timeout_connect=settings.provider_timeout_connect,
            timeout_read=settings.provider_timeout_read,
            headers={"Authorization": f"Bearer {key}"},
        )

        async with client:
            resp = await call_with_retry(
                lambda: client.post("/chat/completions", json=body),
                provider_name=self.provider_name,
                max_retries=settings.provider_max_retries,
            )
            data = resp.json()

        return parse_openai_response(data)

    async def stream_chat(
        self, payload, messages: list[Message], *, resolved_model: str | None = None, api_key: str | None = None
    ) -> AsyncIterator[ProviderStreamChunk]:
        body = self._build_body(payload, messages, stream=True, resolved_model=resolved_model)
        key = api_key or settings.openai_api_key

        client = create_provider_client(
            base_url=resolve_base_url(self.provider_name, OPENAI_BASE_URL),
            timeout_connect=settings.provider_timeout_connect,
            timeout_read=settings.provider_timeout_read,
            headers={"Authorization": f"Bearer {key}"},
        )

        async with client:
            async with client.stream(
                "POST", "/chat/completions", json=body
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    chunk = _parse_sse_line(line)
                    if chunk is not None:
                        yield chunk


def _parse_sse_line(line: str) -> ProviderStreamChunk | None:
    """Parse a single SSE line from the OpenAI streaming response."""
    if not line.startswith("data: "):
        return None

    data_str = line[6:]
    if data_str.strip() == "[DONE]":
        return None

    try:
        data = json.loads(data_str)
    except json.JSONDecodeError:
        return None

    # The trailing usage chunk emitted under stream_options.include_usage
    # has empty `choices` and a populated `usage`. Surface it so the route
    # layer can record token counts.
    usage_dict = data.get("usage")
    usage = None
    if isinstance(usage_dict, dict):
        # o-series reasoning token count lives under
        # ``usage.completion_tokens_details.reasoning_tokens``. Absent
        # for non-reasoning models / non-OpenAI providers — defaults
        # to 0 then.
        details = usage_dict.get("completion_tokens_details") or {}
        reasoning_tok = (
            int(details.get("reasoning_tokens", 0) or 0)
            if isinstance(details, dict) else 0
        )
        usage = ProviderUsage(
            prompt_tokens=int(usage_dict.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage_dict.get("completion_tokens", 0) or 0),
            total_tokens=int(usage_dict.get("total_tokens", 0) or 0),
            reasoning_tokens=reasoning_tok,
        )

    choices = data.get("choices", [])
    if not choices:
        if usage is not None:
            return ProviderStreamChunk(
                delta="",
                finish_reason=None,
                model=data.get("model", ""),
                usage=usage,
            )
        return None

    delta = choices[0].get("delta", {})
    # Tool-call deltas: OpenAI streams the first chunk with
    # ``[{index, id, type:"function", function:{name, arguments:""}}]``
    # and follow-up chunks with ``[{index, function:{arguments:"…"}}]``
    # appending to the JSON args. We forward each fragment verbatim so
    # the route layer can re-emit it as an SSE delta — the client is
    # responsible for stitching ``function.arguments`` together (same
    # contract as a direct OpenAI stream). ``None`` when absent so a
    # plain text chunk stays a plain text chunk.
    raw_tool_calls = delta.get("tool_calls")
    tool_calls = raw_tool_calls if isinstance(raw_tool_calls, list) and raw_tool_calls else None
    # Reasoning content delta (T2). OpenAI o-series on the Chat
    # Completions API doesn't emit reasoning text in the streaming
    # delta — only the final ``usage.completion_tokens_details.reasoning_tokens``
    # count. But DeepSeek-R1 and Groq's R1-distill DO emit
    # ``delta.reasoning_content`` over the same OpenAI-compatible
    # endpoint, and this adapter's parser is reused by the
    # ``OpenAICompatibleAdapter`` base via ``_parse_sse_line`` — so
    # reading the field here surfaces reasoning for those providers
    # without a parallel parser. ``None`` when absent so plain OpenAI
    # gpt-4o streams are unchanged on the wire.
    reasoning_raw = delta.get("reasoning_content")
    reasoning_delta = reasoning_raw if isinstance(reasoning_raw, str) and reasoning_raw else None
    return ProviderStreamChunk(
        delta=delta.get("content") or "",
        finish_reason=choices[0].get("finish_reason"),
        model=data.get("model", ""),
        usage=usage,
        tool_calls=tool_calls,
        reasoning_delta=reasoning_delta,
    )
