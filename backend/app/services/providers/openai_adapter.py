"""OpenAI Chat Completions adapter.

Calls the OpenAI API via httpx and normalizes the response into the
canonical ProviderResponse.  Supports both blocking and streaming modes.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, AsyncIterator

import httpx

from app.core.config import settings
from app.domain.providers import ProviderResponse, ProviderStreamChunk, ProviderUsage
from app.services.providers.base import BaseProviderAdapter
from app.services.providers.base_urls import resolve_base_url
from app.services.providers.http_client import get_provider_client
from app.services.providers.error_handler import call_with_retry, map_provider_error
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

# Reasoning-summary verbosity passed to the Responses API. ``auto`` lets
# OpenAI pick the granularity; ``detailed`` for the highest effort. (Org
# must be verified for reasoning summaries — when it isn't, OpenAI returns
# no summary items and we transparently surface only the token count, same
# as the Chat Completions path.)
_RESPONSES_SUMMARY_MAP: dict[str, str] = {
    "auto": "auto",
    "low": "auto",
    "medium": "auto",
    "high": "detailed",
}

# Responses API endpoint (vs. /chat/completions). Only the OpenAI o-series
# (reasoning_strategy == "openai_responses_reasoning") routes here, and only
# when the caller wants the visible summary.
_RESPONSES_PATH = "/responses"


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

    @staticmethod
    def _responses_eligible(payload, resolved_model: str | None) -> bool:
        """True when this request should use the Responses API.

        Tightly gated so the default path is untouched:
          * the resolved model is an o-series reasoning model
            (``reasoning_strategy == "openai_responses_reasoning"``),
          * the caller asked for reasoning (``mode != off``) AND wants the
            visible summary (``show_summary``) — the only reason to pay the
            Responses API; token-count-only requests stay on
            /chat/completions, and
          * the request carries NO tools (the Responses API models tool
            calls as ``function_call`` items, a different shape; tool turns
            keep the well-tested Chat Completions path).
        """
        rc = getattr(payload, "reasoning", None)
        if rc is None or rc.mode == "off" or not getattr(rc, "show_summary", False):
            return False
        if getattr(payload, "tools", None):
            return False
        model = resolved_model or payload.model
        from app.services.providers.model_registry import get_candidate

        cand = get_candidate(model)
        return bool(
            cand
            and cand.supports_reasoning
            and cand.reasoning_strategy == "openai_responses_reasoning"
        )

    def _build_responses_body(
        self, payload, messages, *, stream: bool = False, resolved_model: str | None = None
    ) -> dict:
        """Build a Responses API request body (``input`` + ``reasoning``).

        Differs from the Chat Completions body: messages become ``input``
        role/content items, reasoning carries ``effort`` + ``summary``, and
        the token budget is ``max_output_tokens``.
        """
        model = resolved_model or payload.model
        rc = payload.reasoning
        input_items = [
            {"role": m.role, "content": (m.content if m.content is not None else "")}
            for m in messages
        ]
        body: dict = {
            "model": model,
            "input": input_items,
            "reasoning": {
                "effort": _REASONING_EFFORT_MAP.get(rc.mode, "medium"),
                "summary": _RESPONSES_SUMMARY_MAP.get(rc.mode, "auto"),
            },
        }
        if stream:
            body["stream"] = True
        mt = getattr(payload, "max_tokens", None) or payload.metadata.get("max_tokens")
        if mt is not None:
            body["max_output_tokens"] = mt
        return body

    async def _chat_responses(
        self, payload, messages, *, resolved_model, key, client, headers
    ) -> ProviderResponse:
        body = self._build_responses_body(payload, messages, resolved_model=resolved_model)
        resp = await call_with_retry(
            lambda: client.post(_RESPONSES_PATH, json=body, headers=headers),
            provider_name=self.provider_name,
            max_retries=settings.provider_max_retries,
        )
        resp.raise_for_status()
        return _parse_responses_response(resp.json())

    async def chat(
        self, payload, messages: list[Message], *, resolved_model: str | None = None, api_key: str | None = None
    ) -> ProviderResponse:
        key = api_key or settings.openai_api_key

        client = await get_provider_client(
            base_url=resolve_base_url(self.provider_name, OPENAI_BASE_URL),
            timeout_connect=settings.provider_timeout_connect,
            timeout_read=settings.provider_timeout_read,
        )
        headers = {"Authorization": f"Bearer {key}"}

        # Responses API for o-series visible reasoning summaries (P2). On any
        # failure we transparently fall back to /chat/completions (token
        # count only) so a Responses outage / unverified-org never breaks the
        # request — the model still answers, just without the summary text.
        if self._responses_eligible(payload, resolved_model):
            try:
                return await self._chat_responses(
                    payload, messages,
                    resolved_model=resolved_model, key=key,
                    client=client, headers=headers,
                )
            except Exception:
                import structlog
                structlog.get_logger(__name__).warning(
                    "openai_responses_fallback_to_chat",
                    model=resolved_model or payload.model,
                )

        body = self._build_body(payload, messages, resolved_model=resolved_model)
        resp = await call_with_retry(
            lambda: client.post("/chat/completions", json=body, headers=headers),
            provider_name=self.provider_name,
            max_retries=settings.provider_max_retries,
        )
        data = resp.json()

        return parse_openai_response(data)

    async def stream_chat(
        self, payload, messages: list[Message], *, resolved_model: str | None = None, api_key: str | None = None
    ) -> AsyncIterator[ProviderStreamChunk]:
        key = api_key or settings.openai_api_key

        client = await get_provider_client(
            base_url=resolve_base_url(self.provider_name, OPENAI_BASE_URL),
            timeout_connect=settings.provider_timeout_connect,
            timeout_read=settings.provider_timeout_read,
        )
        headers = {"Authorization": f"Bearer {key}"}

        # Responses API streaming for o-series visible reasoning (P2). Emits
        # ``response.reasoning_summary_text.delta`` (→ reasoning_delta) and
        # ``response.output_text.delta`` (→ content). Falls back to
        # /chat/completions ONLY if the Responses stream fails BEFORE the
        # first chunk — once bytes are on the wire we must not switch bodies
        # mid-stream (the route layer's own fallback handles later errors).
        if self._responses_eligible(payload, resolved_model):
            r_body = self._build_responses_body(
                payload, messages, stream=True, resolved_model=resolved_model
            )
            yielded = False
            try:
                async with client.stream(
                    "POST", _RESPONSES_PATH, json=r_body, headers=headers
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        chunk = _parse_responses_sse_line(line)
                        if chunk is not None:
                            yielded = True
                            yield chunk
                return
            except Exception:
                if yielded:
                    raise
                import structlog
                structlog.get_logger(__name__).warning(
                    "openai_responses_stream_fallback_to_chat",
                    model=resolved_model or payload.model,
                )

        body = self._build_body(payload, messages, stream=True, resolved_model=resolved_model)
        async with client.stream(
            "POST", "/chat/completions", json=body, headers=headers
        ) as resp:
            # Map an HTTP error to a domain exception BEFORE any chunk
            # is yielded so the route layer's streaming-fallback loop
            # can classify it — a raw httpx.HTTPStatusError fails its
            # GatewayError gate and would skip fallback. aread() loads
            # the body so map_provider_error can parse the payload.
            if resp.status_code >= 400:
                await resp.aread()
                try:
                    resp.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise map_provider_error(exc, self.provider_name) from exc
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
        # Cached-prompt hit under ``prompt_tokens_details.cached_tokens``
        # on the trailing usage chunk (OpenAI). 0 when absent.
        prompt_details = usage_dict.get("prompt_tokens_details") or {}
        cached_tok = (
            int(prompt_details.get("cached_tokens", 0) or 0)
            if isinstance(prompt_details, dict) else 0
        )
        usage = ProviderUsage(
            prompt_tokens=int(usage_dict.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage_dict.get("completion_tokens", 0) or 0),
            total_tokens=int(usage_dict.get("total_tokens", 0) or 0),
            reasoning_tokens=reasoning_tok,
            cached_prompt_tokens=cached_tok,
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
    # Two field names in the wild: DeepSeek-native / Groq R1-distill emit
    # ``delta.reasoning_content``; OpenRouter normalises the SAME models'
    # thinking into ``delta.reasoning`` (P1 Item 3 — this mismatch was why
    # ``openrouter/deepseek/deepseek-r1`` thinking never reached the UI).
    # Read both so reasoning streams regardless of which spelling the
    # upstream uses; ``reasoning_content`` wins when both are present.
    reasoning_raw = delta.get("reasoning_content") or delta.get("reasoning")
    reasoning_delta = reasoning_raw if isinstance(reasoning_raw, str) and reasoning_raw else None
    return ProviderStreamChunk(
        delta=delta.get("content") or "",
        finish_reason=choices[0].get("finish_reason"),
        model=data.get("model", ""),
        usage=usage,
        tool_calls=tool_calls,
        reasoning_delta=reasoning_delta,
    )


def _responses_usage(usage_dict: dict | None) -> ProviderUsage | None:
    """Map a Responses API ``usage`` block to ProviderUsage.

    Responses uses ``input_tokens`` / ``output_tokens`` (vs prompt/
    completion) and nests the reasoning count under
    ``output_tokens_details.reasoning_tokens``.
    """
    if not isinstance(usage_dict, dict):
        return None
    details = usage_dict.get("output_tokens_details") or {}
    reasoning_tok = (
        int(details.get("reasoning_tokens", 0) or 0)
        if isinstance(details, dict) else 0
    )
    inp = int(usage_dict.get("input_tokens", 0) or 0)
    out = int(usage_dict.get("output_tokens", 0) or 0)
    total = int(usage_dict.get("total_tokens", 0) or 0) or (inp + out)
    return ProviderUsage(
        prompt_tokens=inp,
        completion_tokens=out,
        total_tokens=total,
        reasoning_tokens=reasoning_tok,
    )


def _parse_responses_response(data: dict) -> ProviderResponse:
    """Parse a non-streaming Responses API body into ProviderResponse.

    The ``output`` array carries ``reasoning`` items (with ``summary``
    parts) and ``message`` items (with ``output_text`` parts). Reasoning
    summary text is concatenated into ``reasoning_content``; visible text
    into ``content``. When the org isn't verified for summaries the
    reasoning items carry no summary parts → ``reasoning_content`` is None
    (token count still surfaces via usage), matching the Chat Completions
    behaviour.
    """
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    for item in data.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        itype = item.get("type")
        if itype == "message":
            for c in item.get("content", []) or []:
                if isinstance(c, dict) and c.get("type") == "output_text":
                    text_parts.append(c.get("text", "") or "")
        elif itype == "reasoning":
            for s in item.get("summary", []) or []:
                if isinstance(s, dict) and s.get("type") == "summary_text":
                    reasoning_parts.append(s.get("text", "") or "")
    reasoning_text = "".join(reasoning_parts) or None
    return ProviderResponse(
        content="".join(text_parts),
        model=data.get("model", ""),
        finish_reason="stop",
        usage=_responses_usage(data.get("usage")),
        raw=data,
        reasoning_content=reasoning_text,
    )


def _parse_responses_sse_line(line: str) -> ProviderStreamChunk | None:
    """Parse one Responses API streaming SSE line.

    Responses streams emit ``event:`` + ``data:`` line pairs; the ``data``
    JSON itself carries a ``type`` discriminator, so we dispatch on that
    and ignore the ``event:`` line. Handled types:
      * ``response.output_text.delta``            → content delta
      * ``response.reasoning_summary_text.delta`` → reasoning delta
      * ``response.completed``                    → final usage
    All other lifecycle events (created / in_progress / *.added / *.done)
    are ignored.
    """
    if not line.startswith("data: "):
        return None
    data_str = line[6:]
    if data_str.strip() == "[DONE]":
        return None
    try:
        data = json.loads(data_str)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    t = data.get("type")
    if t == "response.output_text.delta":
        return ProviderStreamChunk(
            delta=data.get("delta", "") or "", finish_reason=None, model=""
        )
    if t == "response.reasoning_summary_text.delta":
        rd = data.get("delta", "") or ""
        return ProviderStreamChunk(
            delta="", finish_reason=None, model="",
            reasoning_delta=rd or None,
        )
    if t == "response.completed":
        resp_obj = data.get("response") or {}
        return ProviderStreamChunk(
            delta="",
            finish_reason="stop",
            model=resp_obj.get("model", "") if isinstance(resp_obj, dict) else "",
            usage=_responses_usage(
                resp_obj.get("usage") if isinstance(resp_obj, dict) else None
            ),
        )
    return None
