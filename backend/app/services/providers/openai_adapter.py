"""OpenAI Chat Completions adapter.

Calls the OpenAI API via httpx and normalizes the response into the
canonical ProviderResponse.  Supports both blocking and streaming modes.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, AsyncIterator

import httpx
import structlog

from app.core.config import settings
from app.domain.providers import ProviderResponse, ProviderStreamChunk, ProviderUsage
from app.services.providers.base import BaseProviderAdapter
from app.services.providers.base_urls import resolve_base_url
from app.services.providers.http_client import get_provider_client
from app.services.providers.error_handler import call_with_retry, map_provider_error
from app.services.providers.parsers import parse_openai_response

if TYPE_CHECKING:
    from app.schemas.common import Message

log = structlog.get_logger(__name__)

OPENAI_BASE_URL = "https://api.openai.com/v1"


def _responses_failure_fields(exc: Exception) -> dict:
    """Loggable detail for a Responses-API failure that triggers fallback.

    The non-stream path raises domain exceptions from ``call_with_retry``
    whose ``details`` already carry the upstream status + body; the stream
    path raises a mapped error the same way. A raw ``httpx.HTTPStatusError``
    (possible on future call sites) is read directly; its body may be
    unavailable when the response was streamed and never read.
    """
    fields: dict = {
        "error_type": type(exc).__name__,
        "error": str(exc)[:500],
    }
    details = getattr(exc, "details", None)
    if isinstance(details, dict):
        if "status" in details:
            fields["status"] = details["status"]
        if "body" in details:
            fields["body"] = details["body"]
    elif isinstance(exc, httpx.HTTPStatusError):
        fields["status"] = exc.response.status_code
        try:
            fields["body"] = exc.response.json()
        except Exception:
            try:
                fields["body"] = exc.response.text[:500]
            except Exception:
                fields["body"] = "<body not read>"
    return fields

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

# Model families that use the post-2024 /chat/completions sampling
# contract: ``max_completion_tokens`` instead of ``max_tokens``, and only
# default ``temperature`` / ``top_p``. THE single place the rule lives —
# adding the next family (e.g. "gpt-6") is a one-entry change here.
#
# Deliberately NAME-based, not catalog-based: this is wire-protocol
# behaviour (which sampling params the endpoint accepts), not a
# capability an admin should be able to flip — a wrong data row must
# never be able to break request shaping. (Historical note: the catalog
# flags for gpt-5 rows WERE wrong until migrations 074/075.) The
# accepted trade-off: a family whose name doesn't start with one of
# these prefixes needs a new entry; in exchange the rule is correct for
# every current row (including the oddly-named ``gpt-5.6-luna`` /
# ``-sol`` / ``-terra``, which all share the ``gpt-5`` prefix) and
# depends on no data we know to be wrong.
#
# Adding the next family: append its prefix if the provider rejects
# legacy ``max_tokens`` for it (probe: a 400 naming max_completion_tokens).
_COMPLETION_TOKEN_FAMILIES: tuple[str, ...] = ("gpt-5", "o1", "o3", "o4")

# Families that reason BY DEFAULT server-side and whose /chat/completions
# contract rejects function tools at any reasoning effort except an
# explicit ``"none"`` (400 invalid_request_error, param=reasoning_effort
# — even when the request carries no reasoning fields at all; the effort
# defaulting happens on OpenAI's side). Verified live against the whole
# gpt-5.6 generation (``-luna`` / ``-sol`` / ``-terra``); gpt-5.1
# through gpt-5.4 accept tools at their default effort and MUST NOT get
# the pin, since it would silently disable reasoning they support.
_TOOLS_REQUIRE_EFFORT_NONE_FAMILIES: tuple[str, ...] = ("gpt-5.6",)
# Adding the next family: append its prefix ONLY if a live probe shows
# the provider 400s an effort-LESS tools request (server-side default
# reasoning); families that merely reject tools+explicit-effort are
# already covered by the pin in _build_body.

# Families whose Responses-API tier is "full": eligible WITH tools, not
# just for summary-only turns. /chat/completions rejects tools at any
# effort except an explicit 'none' for this generation, so /v1/responses
# is the only endpoint serving reasoning AND tools together (the
# o-series shares reasoning_strategy='openai_responses_reasoning' in the
# catalog but deliberately stays on the conservative summary-only tier —
# its chat/completions accepts tools at any effort, and its Responses
# summaries are org-verification-gated).
#
# Adding the next family: if its chat/completions rejects tools+effort
# the way gpt-5 does (probe it live), append the prefix here AND to
# _COMPLETION_TOKEN_FAMILIES; eligibility itself needs no code change —
# it is keyed off the catalog row's reasoning_strategy.
_RESPONSES_WITH_TOOLS_FAMILIES: tuple[str, ...] = ("gpt-5",)


def _model_in_family(model: str, families: tuple[str, ...]) -> bool:
    """Prefix match with a boundary check so ``gpt-5`` matches ``gpt-5``,
    ``gpt-5.4-mini`` and ``gpt-5.6-luna`` but a hypothetical ``gpt-50``
    or ``o1000x`` would not silently inherit the family contract.
    """
    for family in families:
        if model == family:
            return True
        if model.startswith(family) and len(model) > len(family) and model[len(family)] in ".-":
            return True
    return False


def _uses_completion_token_contract(model: str) -> bool:
    """True for OpenAI families that reject legacy ``max_tokens``."""
    return _model_in_family(model, _COMPLETION_TOKEN_FAMILIES)


class OpenAIAdapter(BaseProviderAdapter):
    provider_name = "openai"
    supports_streaming = True

    @staticmethod
    def _serialize_message(msg) -> dict:
        """Build the OpenAI wire-format dict for a single Message.

        Includes content when set. Function-calling fields are role-scoped:
        assistant turns may carry ``tool_calls`` while tool result turns may
        carry ``tool_call_id`` / ``name``. Other internal message metadata
        (for example desktop screenshot markers on user turns) must not leak
        into the OpenAI wire payload.
        """
        out: dict = {"role": msg.role}
        if msg.content is not None:
            out["content"] = msg.content
        if msg.role == "assistant" and msg.tool_calls is not None:
            out["tool_calls"] = msg.tool_calls
        if msg.role == "tool":
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

        # Post-2024 OpenAI families (GPT-5.x and the o-series) use a
        # different sampling contract on /chat/completions: ``max_tokens``
        # is rejected outright ("Use 'max_completion_tokens' instead",
        # 400 unsupported_parameter — the live failure behind "Model
        # unavailable, please try again"), and non-default ``temperature``
        # / ``top_p`` are rejected the same way. Shape by MODEL FAMILY,
        # not by reasoning mode: the old rename below only fired when the
        # caller asked for reasoning AND the catalog flagged the model
        # ``supports_reasoning`` — the GPT-5 rows are seeded with that
        # flag false, and the desktop never sends a reasoning config, so
        # plain requests went out with ``max_tokens`` and died. gpt-4-era
        # models skip this block entirely and keep today's body
        # byte-for-byte.
        if _uses_completion_token_contract(model):
            if "max_tokens" in body:
                body["max_completion_tokens"] = body.pop("max_tokens")
            body.pop("temperature", None)
            body.pop("top_p", None)

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

        # PRECEDENCE — this block must stay LAST in the builder, after
        # the reasoning block above, because on /chat/completions the
        # whole gpt-5 generation rejects function tools at any
        # reasoning effort except an explicit 'none' (400, live-proven
        # against gpt-5.4-mini AND gpt-5.6, 2026-08-17). Two cases:
        #
        #  1. Tools + an effort chosen above (reasoning wanted but this
        #     request is on the chat/completions path — i.e. the
        #     Responses attempt failed and fell back here): OVERRIDE to
        #     'none'. Answer-over-reasoning is the fallback's contract;
        #     the earlier ordering bug (reasoning block silently beating
        #     the pin) must not come back.
        #  2. gpt-5.6 family + tools with NO effort chosen: pin 'none'
        #     anyway — 5.6 reasons by server default, so even an
        #     effort-less tools request 400s without the explicit opt-out.
        #
        # Reasoning-WITH-tools is served by the Responses path
        # (_responses_eligible), never by this builder.
        if "tools" in body and _model_in_family(model, _RESPONSES_WITH_TOOLS_FAMILIES):
            if "reasoning_effort" in body or _model_in_family(
                model, _TOOLS_REQUIRE_EFFORT_NONE_FAMILIES
            ):
                body["reasoning_effort"] = "none"

        return body

    @staticmethod
    def _responses_eligible(payload, resolved_model: str | None) -> bool:
        """True when this request should use the Responses API.

        Two tiers, both requiring a catalog row with
        ``supports_reasoning=True`` and ``reasoning_strategy ==
        "openai_responses_reasoning"`` and a non-``off`` reasoning mode:

        * **_RESPONSES_WITH_TOOLS_FAMILIES (gpt-5)** — eligible with or
          without tools. On
          /chat/completions this generation rejects function tools at
          any reasoning effort except an explicit 'none' ("To use
          function tools, use /v1/responses" — the provider's own 400),
          so /responses is the ONLY endpoint that serves reasoning AND
          tools together. Live-verified 2026-08-17: tool call + summary
          text arrive together, and a stateless follow-up leg
          (function_call + function_call_output echoed back, no
          reasoning item) is accepted.

        * **o-series** (everything else with the strategy) — the
          original conservative gate, byte-identical to before: summary
          wanted AND no tools. Tool turns keep the well-tested Chat
          Completions path, which the o-series accepts at any effort.
        """
        rc = getattr(payload, "reasoning", None)
        if rc is None or rc.mode == "off":
            return False
        model = resolved_model or payload.model
        from app.services.providers.model_registry import get_candidate

        cand = get_candidate(model)
        if not (
            cand
            and cand.supports_reasoning
            and cand.reasoning_strategy == "openai_responses_reasoning"
        ):
            return False
        if _model_in_family(model, _RESPONSES_WITH_TOOLS_FAMILIES):
            return True
        if getattr(payload, "tools", None):
            return False
        return bool(getattr(rc, "show_summary", False))

    @staticmethod
    def _messages_to_responses_input(messages) -> list[dict]:
        """Translate Chat-Completions-shaped history into Responses input.

        Plain role turns become ``{role, content}`` items. Function-calling
        history changes shape entirely: an assistant turn's ``tool_calls``
        become top-level ``function_call`` items (call_id / name /
        arguments), and each ``tool`` role turn becomes a
        ``function_call_output`` item keyed by the same call_id. Prior
        reasoning items are NOT replayed — the client contract has no slot
        for them, and the stateless echo (function_call + output, no
        reasoning sibling) is accepted upstream (live-verified 2026-08-17).
        """
        items: list[dict] = []
        for m in messages:
            if m.role == "assistant" and m.tool_calls:
                # Content (if any) stays a message item; each tool call
                # becomes its own function_call item.
                if m.content:
                    items.append({"role": "assistant", "content": m.content})
                for tc in m.tool_calls:
                    fn = tc.get("function") or {}
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": tc.get("id", ""),
                            "name": fn.get("name", ""),
                            "arguments": fn.get("arguments", "") or "",
                        }
                    )
                continue
            if m.role == "tool":
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": m.tool_call_id or "",
                        "output": m.content if m.content is not None else "",
                    }
                )
                continue
            items.append(
                {"role": m.role, "content": (m.content if m.content is not None else "")}
            )
        return items

    def _build_responses_body(
        self, payload, messages, *, stream: bool = False, resolved_model: str | None = None
    ) -> dict:
        """Build a Responses API request body (``input`` + ``reasoning``).

        Differs from the Chat Completions body: messages become ``input``
        items (see ``_messages_to_responses_input`` for the tool-history
        translation), tool definitions are FLAT (``{type, name,
        description, parameters}`` — not nested under ``function``),
        reasoning carries ``effort`` (+ ``summary`` only when the caller
        wants it visible), and the token budget is ``max_output_tokens``.
        """
        model = resolved_model or payload.model
        rc = payload.reasoning
        reasoning_block: dict = {
            "effort": _REASONING_EFFORT_MAP.get(rc.mode, "medium"),
        }
        if getattr(rc, "show_summary", False):
            reasoning_block["summary"] = _RESPONSES_SUMMARY_MAP.get(rc.mode, "auto")
        body: dict = {
            "model": model,
            "input": self._messages_to_responses_input(messages),
            "reasoning": reasoning_block,
        }
        tools = getattr(payload, "tools", None)
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "name": (t.get("function") or {}).get("name", ""),
                    "description": (t.get("function") or {}).get("description", ""),
                    "parameters": (t.get("function") or {}).get("parameters", {}),
                }
                if t.get("type") == "function" and "function" in t
                else t
                for t in tools
            ]
            tool_choice = getattr(payload, "tool_choice", None)
            if tool_choice is not None:
                # String forms ("auto"/"required"/"none") pass through;
                # the named form flattens the same way the defs do.
                if isinstance(tool_choice, dict) and "function" in tool_choice:
                    body["tool_choice"] = {
                        "type": "function",
                        "name": (tool_choice.get("function") or {}).get("name", ""),
                    }
                else:
                    body["tool_choice"] = tool_choice
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
            provider_name=self.provider_name,
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
            except Exception as exc:
                # Permanent silent degradation is not a debug detail:
                # log WHY the Responses path failed (status + provider
                # body when it was an HTTP error) so a 100%-fallback
                # situation is diagnosable from the logs alone. The
                # fallback itself is unchanged — the user still gets a
                # /chat/completions answer.
                log.warning(
                    "openai_responses_fallback_to_chat",
                    model=resolved_model or payload.model,
                    **_responses_failure_fields(exc),
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
            provider_name=self.provider_name,
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
                    # Same pattern as the /chat/completions stream branch
                    # below: read the error body BEFORE raising so the
                    # fallback handler can log the provider's actual
                    # message instead of an unread-stream placeholder.
                    if resp.status_code >= 400:
                        await resp.aread()
                        try:
                            resp.raise_for_status()
                        except httpx.HTTPStatusError as status_exc:
                            raise map_provider_error(
                                status_exc, self.provider_name
                            ) from status_exc
                    # Per-stream tool-call bookkeeping (item_id → dense
                    # downstream index) owned here, not in the parser.
                    sse_state: dict = {}
                    async for line in resp.aiter_lines():
                        chunk = _parse_responses_sse_line(line, sse_state)
                        if chunk is not None:
                            yielded = True
                            yield chunk
                return
            except Exception as exc:
                if yielded:
                    raise
                log.warning(
                    "openai_responses_stream_fallback_to_chat",
                    model=resolved_model or payload.model,
                    **_responses_failure_fields(exc),
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
    tool_calls: list[dict] = []
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
        elif itype == "function_call":
            # Normalize to the Chat-Completions tool_calls shape the
            # whole downstream (route layer, DLP arg buffering, both
            # clients) already speaks — same pattern as the Anthropic
            # parser's tool_use normalization (parsers.py).
            tool_calls.append(
                {
                    "id": item.get("call_id", "") or item.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": item.get("name", ""),
                        "arguments": item.get("arguments", "") or "",
                    },
                }
            )
    reasoning_text = "".join(reasoning_parts) or None
    return ProviderResponse(
        content="".join(text_parts),
        model=data.get("model", ""),
        finish_reason="tool_calls" if tool_calls else "stop",
        usage=_responses_usage(data.get("usage")),
        raw=data,
        reasoning_content=reasoning_text,
        tool_calls=tool_calls or None,
    )


def _parse_responses_sse_line(
    line: str, state: dict | None = None
) -> ProviderStreamChunk | None:
    """Parse one Responses API streaming SSE line.

    Responses streams emit ``event:`` + ``data:`` line pairs; the ``data``
    JSON itself carries a ``type`` discriminator, so we dispatch on that
    and ignore the ``event:`` line. Handled types:
      * ``response.output_text.delta``            → content delta
      * ``response.reasoning_summary_text.delta`` → reasoning delta
      * ``response.output_item.added`` (function_call)
                                                  → tool_calls header delta
      * ``response.function_call_arguments.delta``→ tool_calls args delta
      * ``response.completed``                    → final usage
    All other lifecycle events (created / in_progress / *.added / *.done)
    are ignored.

    ``state`` carries the per-stream tool-call bookkeeping the caller
    owns (Responses keys argument deltas by ``item_id``; the downstream
    Chat-Completions delta contract keys by a dense 0-based ``index``
    with the id+name on the first fragment). Passing ``None`` keeps the
    text/reasoning/usage parsing stateless for tool-less callers.
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
    if t == "response.output_item.added" and state is not None:
        item = data.get("item") or {}
        if isinstance(item, dict) and item.get("type") == "function_call":
            index = len(state.setdefault("tool_indexes", {}))
            state["tool_indexes"][item.get("id", "")] = index
            return ProviderStreamChunk(
                delta="", finish_reason=None, model="",
                tool_calls=[
                    {
                        "index": index,
                        "id": item.get("call_id", "") or item.get("id", ""),
                        "type": "function",
                        "function": {"name": item.get("name", ""), "arguments": ""},
                    }
                ],
            )
        return None
    if t == "response.function_call_arguments.delta" and state is not None:
        indexes = state.get("tool_indexes") or {}
        index = indexes.get(data.get("item_id", ""))
        if index is None:
            return None
        return ProviderStreamChunk(
            delta="", finish_reason=None, model="",
            tool_calls=[
                {"index": index, "function": {"arguments": data.get("delta", "") or ""}}
            ],
        )
    if t == "response.completed":
        resp_obj = data.get("response") or {}
        saw_tools = bool(state and state.get("tool_indexes"))
        return ProviderStreamChunk(
            delta="",
            finish_reason="tool_calls" if saw_tools else "stop",
            model=resp_obj.get("model", "") if isinstance(resp_obj, dict) else "",
            usage=_responses_usage(
                resp_obj.get("usage") if isinstance(resp_obj, dict) else None
            ),
        )
    return None
