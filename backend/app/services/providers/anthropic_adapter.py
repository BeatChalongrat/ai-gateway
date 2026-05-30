"""Anthropic Messages API adapter.

Translates between the gateway's canonical format and Anthropic's
Messages API, which differs from OpenAI in several ways:
- System message is a top-level ``system`` parameter, not in messages
- Response content is a list of content blocks, not a single string
- ``stop_reason`` instead of ``finish_reason``
- ``input_tokens`` / ``output_tokens`` instead of ``prompt_tokens`` / ``completion_tokens``
- Streaming uses a multi-event SSE state machine (see ``_process_sse_stream``)
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, AsyncIterator

from app.core.config import settings
from app.domain.providers import (
    ProviderResponse,
    ProviderStreamChunk,
    ProviderUsage,
)
from app.services.providers.base import BaseProviderAdapter
from app.services.providers.base_urls import resolve_base_url
from app.services.providers.http_client import get_provider_client
from app.services.providers.error_handler import call_with_retry
from app.services.providers.parsers import parse_anthropic_response

if TYPE_CHECKING:
    from app.schemas.common import Message

ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 1024

# Anthropic stop_reason → OpenAI-style finish_reason mapping so gateway
# clients see a consistent vocabulary regardless of which adapter served
# the request. Unknown stop_reasons fall through untranslated.
_STOP_REASON_MAP: dict[str, str] = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
}

# Reasoning-mode → Anthropic thinking budget_tokens. ``auto`` resolves
# to the mid-tier so a caller that hasn't tuned the knob gets a usable
# default. ``off`` is filtered out before this table is consulted.
_REASONING_BUDGET_TOKENS: dict[str, int] = {
    "auto": 4096,
    "low": 2048,
    "medium": 4096,
    "high": 8192,
}


class AnthropicAdapter(BaseProviderAdapter):
    provider_name = "anthropic"
    supports_streaming = True

    def _build_body(
        self,
        payload,
        messages: list["Message"],
        *,
        stream: bool = False,
        resolved_model: str | None = None,
    ) -> dict:
        """Build the JSON body for POST /messages.

        Shared between ``chat`` and ``stream_chat`` so the non-stream
        code path is unchanged by the streaming feature. Extracted
        here so a single function owns payload → Anthropic translation.
        """
        system_text, user_messages = self._split_system(messages)
        model = resolved_model or payload.model
        max_tokens = (
            getattr(payload, "max_tokens", None)
            or payload.metadata.get("max_tokens", DEFAULT_MAX_TOKENS)
        )

        body: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [self._serialize_message(m) for m in user_messages],
        }
        if system_text:
            body["system"] = system_text
        if stream:
            body["stream"] = True

        for param in ("temperature", "top_p"):
            val = getattr(payload, param, None) or payload.metadata.get(param)
            if val is not None:
                body[param] = val

        # Extended thinking (T2). Three things have to be true:
        #   1. Caller sent ``payload.reasoning`` with mode != "off".
        #   2. Resolved model is flagged ``supports_reasoning=True``
        #      in ``model_configs`` (set by migration 045 for Claude 4
        #      family + the rows discovery later surfaces).
        # If both hold we emit the ``thinking`` block, force
        # ``temperature=1`` (Anthropic rejects other values when
        # thinking is on), and bump ``max_tokens`` to ensure the
        # response has room for the thinking budget PLUS the visible
        # answer — Anthropic requires ``max_tokens > thinking.budget``.
        # If either gate fails, we send a plain request and the
        # ``reasoning`` field is silently ignored (same posture every
        # adapter follows for unsupported-on-this-model params).
        reasoning_cfg = getattr(payload, "reasoning", None)
        if reasoning_cfg is not None and reasoning_cfg.mode != "off":
            from app.services.providers.model_registry import get_candidate
            candidate = get_candidate(model)
            if candidate is not None and candidate.supports_reasoning:
                budget = _REASONING_BUDGET_TOKENS.get(
                    reasoning_cfg.mode, _REASONING_BUDGET_TOKENS["auto"]
                )
                body["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": budget,
                }
                # Anthropic requires temperature=1 with thinking on.
                body["temperature"] = 1
                # top_p / top_k are also disallowed with thinking — drop
                # if present so a caller's stale temperature/top_p
                # request doesn't 400 us.
                body.pop("top_p", None)
                # Ensure max_tokens > budget. Pad with 4096 so the
                # final answer has its own headroom on top of the
                # thinking budget.
                if body["max_tokens"] <= budget:
                    body["max_tokens"] = budget + 4096

        # Function-calling passthrough. Clients send the OpenAI
        # tools/tool_choice shape; this adapter translates to Anthropic
        # ``input_schema`` form and the ``{type: auto|any|tool|none}``
        # tool_choice vocabulary. Only forwards when set so a plain
        # text request stays unchanged.
        tools = getattr(payload, "tools", None)
        if tools:
            translated = self._translate_tools(tools)
            if translated:
                body["tools"] = translated
        tool_choice = getattr(payload, "tool_choice", None)
        if tool_choice is not None:
            translated_choice = self._translate_tool_choice(tool_choice)
            if translated_choice is not None:
                body["tool_choice"] = translated_choice

        return body

    @staticmethod
    def _serialize_message(msg) -> dict:
        """Convert a canonical Message to Anthropic's wire format.

        Three shapes:

        - ``role="tool"`` → ``{"role": "user", "content": [{"type":
          "tool_result", "tool_use_id": ..., "content": ...}]}``.
          Anthropic carries tool results inside a user turn rather
          than as a dedicated role.

        - ``role="assistant"`` with ``tool_calls`` → assistant turn
          whose content is a list of mixed text + ``tool_use`` blocks.
          Text blocks come first (the model's narration, if any) then
          one ``tool_use`` block per tool call. OpenAI keeps tool-call
          arguments as a JSON *string*; Anthropic expects ``input`` as
          a JSON *object* — we ``json.loads`` and fall back to ``{}``
          on a malformed string rather than failing the whole request.

        - Plain user / assistant / system text → ``{"role": ...,
          "content": ...}`` (system is split out upstream by
          ``_split_system`` so it doesn't actually reach this method).
        """
        if msg.role == "tool":
            return {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": msg.tool_call_id or "",
                        "content": msg.content or "",
                    }
                ],
            }

        if msg.role == "assistant" and msg.tool_calls:
            blocks: list[dict] = []
            if msg.content:
                blocks.append({"type": "text", "text": msg.content})
            for tc in msg.tool_calls:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                args_raw = fn.get("arguments", "{}")
                if isinstance(args_raw, str):
                    try:
                        args_obj = json.loads(args_raw)
                    except json.JSONDecodeError:
                        args_obj = {}
                else:
                    args_obj = args_raw or {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.get("id", "") if isinstance(tc, dict) else "",
                        "name": fn.get("name", ""),
                        "input": args_obj,
                    }
                )
            return {"role": "assistant", "content": blocks}

        return {"role": msg.role, "content": msg.content or ""}

    @staticmethod
    def _translate_tools(openai_tools: list[dict]) -> list[dict]:
        """OpenAI ``[{type: function, function: {name, description,
        parameters}}, …]`` → Anthropic ``[{name, description,
        input_schema}, …]``.

        Non-``function`` entries are dropped — Anthropic only supports
        function-type tools today. Missing ``parameters`` defaults to
        ``{"type": "object"}`` (the empty-object schema Anthropic
        accepts as "no arguments").
        """
        out: list[dict] = []
        for t in openai_tools:
            if not isinstance(t, dict) or t.get("type") != "function":
                continue
            fn = t.get("function") or {}
            entry: dict = {
                "name": fn.get("name", ""),
                "input_schema": fn.get("parameters") or {"type": "object"},
            }
            description = fn.get("description")
            if description:
                entry["description"] = description
            out.append(entry)
        return out

    @staticmethod
    def _translate_tool_choice(openai_choice):
        """Map OpenAI ``tool_choice`` → Anthropic ``tool_choice``.

        - ``"auto"`` → ``{type: auto}``
        - ``"none"`` → ``{type: none}``
        - ``"required"`` → ``{type: any}``
        - ``{type: function, function: {name: X}}`` → ``{type: tool,
          name: X}``

        Unknown shapes return ``None`` so the caller omits the field
        and lets Anthropic apply its default (auto when tools are set).
        """
        if openai_choice == "auto":
            return {"type": "auto"}
        if openai_choice == "none":
            return {"type": "none"}
        if openai_choice == "required":
            return {"type": "any"}
        if isinstance(openai_choice, dict):
            if openai_choice.get("type") == "function":
                fn_name = (openai_choice.get("function") or {}).get("name")
                if fn_name:
                    return {"type": "tool", "name": fn_name}
        return None

    async def chat(
        self, payload, messages: list[Message], *, resolved_model: str | None = None, api_key: str | None = None
    ) -> ProviderResponse:
        body = self._build_body(payload, messages, resolved_model=resolved_model)

        client = await get_provider_client(
            base_url=resolve_base_url(self.provider_name, ANTHROPIC_BASE_URL),
            timeout_connect=settings.provider_timeout_connect,
            timeout_read=settings.provider_timeout_read,
        )
        headers = {
            "x-api-key": api_key or settings.anthropic_api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        }

        resp = await call_with_retry(
            lambda: client.post("/messages", json=body, headers=headers),
            provider_name=self.provider_name,
            max_retries=settings.provider_max_retries,
        )
        data = resp.json()

        return parse_anthropic_response(data)

    async def stream_chat(
        self,
        payload,
        messages: list["Message"],
        *,
        resolved_model: str | None = None,
        api_key: str | None = None,
    ) -> AsyncIterator[ProviderStreamChunk]:
        """Stream an Anthropic Messages API response as canonical chunks.

        Connects to ``/v1/messages`` with ``stream: true`` and runs the
        raw SSE lines through ``_process_sse_stream`` which owns the
        event state machine. Splitting transport from parsing keeps
        the parser unit-testable without httpx mocks.
        """
        body = self._build_body(
            payload, messages, stream=True, resolved_model=resolved_model
        )
        key = api_key or settings.anthropic_api_key
        initial_model = resolved_model or payload.model

        client = await get_provider_client(
            base_url=resolve_base_url(self.provider_name, ANTHROPIC_BASE_URL),
            timeout_connect=settings.provider_timeout_connect,
            timeout_read=settings.provider_timeout_read,
        )
        headers = {
            "x-api-key": key,
            "anthropic-version": ANTHROPIC_VERSION,
        }

        async with client.stream(
            "POST", "/messages", json=body, headers=headers
        ) as resp:
            resp.raise_for_status()
            async for chunk in self._process_sse_stream(
                resp.aiter_lines(), initial_model=initial_model
            ):
                yield chunk

    async def _process_sse_stream(
        self,
        lines: AsyncIterator[str],
        *,
        initial_model: str,
    ) -> AsyncIterator[ProviderStreamChunk]:
        """Translate raw SSE lines from /messages into ProviderStreamChunks.

        Anthropic's wire format is two-line-per-event:

            event: content_block_delta
            data: {"type": ..., "delta": {"text": "..."}}

        The state machine accumulates:
        - ``current_event`` — most recent ``event:`` line's type
        - ``input_tokens`` — from ``message_start.usage``
        - ``output_tokens`` — from ``message_delta.usage`` (final value)
        - ``final_model`` — from ``message_start.message.model``
        - ``final_stop`` — from ``message_delta.delta.stop_reason``
          (translated via ``_STOP_REASON_MAP``)
        - ``tool_use_indices`` — set of content-block indices that
          were registered as ``tool_use`` by a prior
          ``content_block_start``. Used to distinguish
          ``input_json_delta`` events belonging to a tool_use from
          stray ones, and to translate Anthropic's per-content-block
          ``index`` into the OpenAI tool_calls delta shape.

        Text deltas and tool_use deltas are yielded as they arrive,
        each in their own ProviderStreamChunk field (``delta`` /
        ``tool_calls``). The downstream SSE emitter forwards each
        independently — a single Anthropic response may interleave
        text and tool_use content blocks, and the client merges them
        by index per the OpenAI streaming contract.

        The final chunk carries finish_reason + usage and is emitted
        on ``message_stop`` so downstream code can record token
        counts even when Anthropic emits them split across events.
        """
        current_event: str | None = None
        input_tokens = 0
        output_tokens = 0
        final_model = initial_model
        final_stop: str | None = None
        # Per-response set of content-block indices we've seen
        # registered as tool_use. Lets the input_json_delta branch
        # safely distinguish "this fragment belongs to a tool call"
        # from "stray delta we don't recognise" without re-checking
        # the (possibly missing) content_block in the delta event.
        tool_use_indices: set[int] = set()
        # Sibling set for thinking blocks (T2). A ``thinking_delta``
        # event only carries an index; we cross-check against this
        # set to drop strays the same way the tool_use branch does.
        thinking_indices: set[int] = set()

        async for raw in lines:
            line = raw.rstrip("\r")
            if not line:
                # Blank line separates SSE events; ignore.
                continue
            if line.startswith("event: "):
                current_event = line[7:].strip()
                continue
            if not line.startswith("data: "):
                continue

            try:
                data = json.loads(line[6:])
            except json.JSONDecodeError:
                continue

            if current_event == "message_start":
                msg = data.get("message") or {}
                usage = msg.get("usage") or {}
                input_tokens = int(usage.get("input_tokens", 0) or 0)
                # message_start sometimes includes an initial
                # output_tokens=1 counter — capture it as a baseline
                # but message_delta will override with the final value.
                output_tokens = int(usage.get("output_tokens", 0) or 0)
                final_model = msg.get("model") or final_model

            elif current_event == "content_block_start":
                # First chunk for a tool_use block carries its id +
                # name with an empty arguments string — mirrors the
                # OpenAI streaming contract where the first
                # tool_calls fragment introduces the call and later
                # fragments append to function.arguments.
                #
                # The Anthropic "index" is the position of this
                # content block inside the response (0 = first block,
                # which is usually text; 1 = second; …). We pass it
                # straight through as the OpenAI tool_calls index —
                # the client merges fragments by index, so the actual
                # numeric value doesn't have to start at zero.
                block = data.get("content_block") or {}
                if block.get("type") == "tool_use":
                    idx = data.get("index", 0)
                    tool_use_indices.add(idx)
                    yield ProviderStreamChunk(
                        model=final_model,
                        tool_calls=[
                            {
                                "index": idx,
                                "id": block.get("id", ""),
                                "type": "function",
                                "function": {
                                    "name": block.get("name", ""),
                                    "arguments": "",
                                },
                            }
                        ],
                    )
                elif block.get("type") in ("thinking", "redacted_thinking"):
                    # Extended thinking (T2). Register the index so
                    # later ``thinking_delta`` events can be matched
                    # to a known block. ``redacted_thinking`` is
                    # Anthropic's encrypted-thoughts variant — we
                    # accept the index for symmetry but never get
                    # ``thinking_delta`` text for it; deltas come in
                    # as opaque ``data`` blobs we deliberately drop
                    # rather than surface to the client.
                    idx = data.get("index", 0)
                    thinking_indices.add(idx)

            elif current_event == "content_block_delta":
                delta = data.get("delta") or {}
                delta_type = delta.get("type")
                if delta_type == "text_delta":
                    text = delta.get("text") or ""
                    if text:
                        yield ProviderStreamChunk(
                            delta=text, model=final_model
                        )
                elif delta_type == "thinking_delta":
                    # Extended-thinking fragment (T2). Forward only
                    # when paired with a known ``thinking`` content
                    # block (registered above) so a stray delta does
                    # not leak into the reasoning channel. The SSE
                    # emitter in public_chat.py gates emit on
                    # ``payload.reasoning.show_summary``, so the
                    # client never sees these unless the caller asked.
                    idx = data.get("index", 0)
                    if idx not in thinking_indices:
                        continue
                    thought = delta.get("thinking") or ""
                    if thought:
                        yield ProviderStreamChunk(
                            reasoning_delta=thought, model=final_model
                        )
                elif delta_type == "signature_delta":
                    # Cryptographic signature attached to a thinking
                    # block — required when round-tripping thinking
                    # back to Anthropic on a follow-up call but has no
                    # user-facing text. Deliberately dropped here;
                    # T4-or-later work can persist signatures if we
                    # ever need to replay thinking back upstream.
                    continue
                elif delta_type == "input_json_delta":
                    # Tool-use arguments stream as raw partial JSON
                    # fragments. We forward each fragment under
                    # function.arguments — the client is responsible
                    # for stitching them into a complete JSON string
                    # (same contract as an OpenAI tool_calls stream).
                    idx = data.get("index", 0)
                    if idx not in tool_use_indices:
                        # No matching content_block_start for this
                        # index — drop the fragment defensively
                        # rather than emit an orphan tool_calls
                        # entry without an id/name introducer.
                        continue
                    partial = delta.get("partial_json")
                    if not partial:
                        continue
                    yield ProviderStreamChunk(
                        model=final_model,
                        tool_calls=[
                            {
                                "index": idx,
                                "function": {"arguments": partial},
                            }
                        ],
                    )

            elif current_event == "message_delta":
                delta = data.get("delta") or {}
                if "stop_reason" in delta and delta["stop_reason"]:
                    raw_stop = delta["stop_reason"]
                    final_stop = _STOP_REASON_MAP.get(raw_stop, raw_stop)
                usage = data.get("usage") or {}
                if "output_tokens" in usage:
                    output_tokens = int(usage["output_tokens"] or 0)

            elif current_event == "message_stop":
                # Final chunk: finish_reason + usage. Emitted even
                # when text / tool_use deltas already yielded so
                # downstream can attach token counts to this request.
                yield ProviderStreamChunk(
                    delta="",
                    finish_reason=final_stop or "stop",
                    model=final_model,
                    usage=ProviderUsage(
                        prompt_tokens=input_tokens,
                        completion_tokens=output_tokens,
                        total_tokens=input_tokens + output_tokens,
                    ),
                )

            # ping / content_block_stop / error / unknown → deliberate
            # no-op. ping is a keepalive; content_block_stop carries
            # no payload our state machine needs (the message_delta
            # event's stop_reason is the authoritative finish signal);
            # unknown events are forward-compatibility fodder.

    @staticmethod
    def _split_system(
        messages: list[Message],
    ) -> tuple[str | None, list[Message]]:
        """Extract system message(s) from the message list.

        Anthropic expects ``system`` as a top-level parameter, not in messages.
        """
        system_parts: list[str] = []
        user_messages: list[Message] = []
        for m in messages:
            if m.role == "system":
                # content is Optional[str] on Message now; an empty
                # system message has no useful text to thread in.
                if m.content:
                    system_parts.append(m.content)
            else:
                user_messages.append(m)
        system_text = "\n\n".join(system_parts) if system_parts else None
        return system_text, user_messages

