"""Google AI (Gemini) adapter.

Gemini's REST API is NOT OpenAI-compatible. This adapter translates
between the gateway's canonical message format and Gemini's
``generateContent`` / ``streamGenerateContent`` endpoints.

Differences from OpenAI schema we handle:
- Endpoint is per-model: ``/models/{model}:generateContent``
- Authentication via ``?key=`` query parameter (simplest, matches docs)
- Role mapping: gateway "assistant" → Gemini "model"; "system" messages
  are lifted into ``systemInstruction`` (Gemini rejects system in contents)
- Messages shaped as ``{role, parts: [{text}]}``
- Response content lives in ``candidates[0].content.parts[*].text``
- Finish reasons are uppercase (STOP / MAX_TOKENS / SAFETY)
- Usage reported as ``usageMetadata`` with different field names
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, AsyncIterator

import httpx

from app.core.config import settings
from app.domain.providers import ProviderResponse, ProviderStreamChunk, ProviderUsage
from app.services.providers.base import BaseProviderAdapter
from app.services.providers.base_urls import resolve_base_url
from app.services.providers.error_handler import call_with_retry, map_provider_error
from app.services.providers.http_client import get_provider_client

if TYPE_CHECKING:
    from app.schemas.common import Message

GOOGLE_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

# Gemini → canonical finish_reason mapping. Anything else falls through
# lowercased so audit logs show the raw upstream value.
_FINISH_MAP = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
    "SAFETY": "content_filter",
    "RECITATION": "content_filter",
    "BLOCKLIST": "content_filter",
    "PROHIBITED_CONTENT": "content_filter",
    "OTHER": "stop",
}

# Reasoning mode → Gemini ``thinkingConfig.thinkingLevel``. Gemini's
# native vocabulary is uppercase. ``auto`` collapses to MEDIUM —
# matches the Anthropic / OpenAI mode-mapping convention.
_GEMINI_THINKING_LEVEL_MAP: dict[str, str] = {
    "auto": "MEDIUM",
    "low": "LOW",
    "medium": "MEDIUM",
    "high": "HIGH",
}


def _split_system(messages: list[Message]) -> tuple[list[Message], str]:
    """Peel system messages off the front; Gemini takes them separately."""
    system_parts: list[str] = []
    rest: list[Message] = []
    for m in messages:
        if m.role == "system":
            system_parts.append(m.content)
        else:
            rest.append(m)
    return rest, "\n\n".join(system_parts)


def _to_gemini_role(role: str) -> str:
    return "model" if role == "assistant" else "user"


def _build_contents(messages: list[Message]) -> list[dict]:
    return [
        {"role": _to_gemini_role(m.role), "parts": _to_gemini_parts(m.content)}
        for m in messages
    ]


# data:image/png;base64,<payload> — the shape chat-edge ships for both
# current-turn uploads and generated-image history replay.
_DATA_URL_RE = re.compile(r"^data:(image/[a-z0-9.+-]+);base64,(.+)$", re.DOTALL)


def _to_gemini_parts(content) -> list[dict]:
    """Canonical message content → Gemini ``parts``.

    Plain strings keep the legacy single-text-part shape. OpenAI-format
    multimodal arrays (``text`` / ``image_url`` parts with base64 data
    URLs) become ``{"text": …}`` / ``{"inline_data": …}`` parts —
    before this, a content array was embedded verbatim under ``text``
    and Gemini rejected the request, silently breaking vision (uploads
    and generated-image history replay). Unconvertible parts degrade to
    a short text placeholder.
    """
    if not isinstance(content, list):
        return [{"text": content or ""}]
    parts: list[dict] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")
        if ptype == "text":
            parts.append({"text": part.get("text") or ""})
        elif ptype == "image_url":
            url = (part.get("image_url") or {}).get("url") or ""
            m = _DATA_URL_RE.match(url)
            if m:
                parts.append(
                    {"inline_data": {"mime_type": m.group(1), "data": m.group(2)}}
                )
            else:
                parts.append({"text": "[image omitted]"})
    if not parts:
        parts.append({"text": ""})
    return parts


def _parse_response(data: dict) -> ProviderResponse:
    candidates = data.get("candidates") or []
    content = ""
    reasoning_text = ""
    finish_reason = "stop"
    if candidates:
        first = candidates[0]
        parts = (first.get("content") or {}).get("parts") or []
        # T3: Gemini 2.5+ marks reasoning parts with ``thought: true``.
        # Concatenate those into ``reasoning_content`` and surface text
        # parts as the visible ``content`` — same split the Anthropic
        # parser does for ``thinking`` content blocks.
        for p in parts:
            if not isinstance(p, dict):
                continue
            text = p.get("text", "") or ""
            if p.get("thought"):
                reasoning_text += text
            else:
                content += text
        finish_reason = _FINISH_MAP.get(
            first.get("finishReason", "STOP"),
            first.get("finishReason", "stop").lower(),
        )

    usage = None
    meta = data.get("usageMetadata") or {}
    if meta:
        prompt = int(meta.get("promptTokenCount") or 0)
        completion = int(meta.get("candidatesTokenCount") or 0)
        total = int(meta.get("totalTokenCount") or (prompt + completion))
        # T3: Gemini reports reasoning token spend under
        # ``thoughtsTokenCount``. 0 / absent for non-reasoning models.
        reasoning_tok = int(meta.get("thoughtsTokenCount") or 0)
        usage = ProviderUsage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
            reasoning_tokens=reasoning_tok,
        )

    return ProviderResponse(
        content=content,
        model=data.get("modelVersion", "") or "",
        finish_reason=finish_reason,
        usage=usage,
        raw=data,
        reasoning_content=reasoning_text or None,
    )


def _parse_stream_chunk(data: dict) -> ProviderStreamChunk | None:
    """Each SSE chunk from streamGenerateContent is a partial response."""
    candidates = data.get("candidates") or []
    delta = ""
    reasoning_delta = ""
    finish_reason: str | None = None
    if candidates:
        first = candidates[0]
        parts = (first.get("content") or {}).get("parts") or []
        # T3: split parts into reasoning vs visible content the same
        # way the non-stream parser does. A single Gemini chunk may
        # carry BOTH a ``thought=true`` part and a regular text part;
        # we emit one ProviderStreamChunk carrying both fields. The
        # SSE emitter in public_chat.py forwards reasoning_delta and
        # delta independently.
        for p in parts:
            if not isinstance(p, dict):
                continue
            text = p.get("text", "") or ""
            if p.get("thought"):
                reasoning_delta += text
            else:
                delta += text
        raw_finish = first.get("finishReason")
        if raw_finish:
            finish_reason = _FINISH_MAP.get(raw_finish, raw_finish.lower())

    usage = None
    meta = data.get("usageMetadata") or {}
    if meta:
        prompt = int(meta.get("promptTokenCount") or 0)
        completion = int(meta.get("candidatesTokenCount") or 0)
        total = int(meta.get("totalTokenCount") or (prompt + completion))
        reasoning_tok = int(meta.get("thoughtsTokenCount") or 0)
        usage = ProviderUsage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
            reasoning_tokens=reasoning_tok,
        )

    if not delta and not reasoning_delta and finish_reason is None and usage is None:
        return None

    return ProviderStreamChunk(
        delta=delta,
        finish_reason=finish_reason,
        model=data.get("modelVersion", "") or "",
        usage=usage,
        reasoning_delta=reasoning_delta or None,
    )


class GoogleAIAdapter(BaseProviderAdapter):
    provider_name = "google"
    supports_streaming = True

    def _build_body(
        self,
        payload,
        messages: list[Message],
        *,
        resolved_model: str | None = None,
    ) -> dict:
        chat_messages, system_text = _split_system(messages)
        body: dict = {"contents": _build_contents(chat_messages)}

        if system_text:
            body["systemInstruction"] = {"parts": [{"text": system_text}]}

        gen: dict = {}
        temperature = getattr(payload, "temperature", None) or payload.metadata.get("temperature")
        if temperature is not None:
            gen["temperature"] = temperature
        max_tokens = getattr(payload, "max_tokens", None) or payload.metadata.get("max_tokens")
        if max_tokens is not None:
            gen["maxOutputTokens"] = max_tokens
        top_p = getattr(payload, "top_p", None) or payload.metadata.get("top_p")
        if top_p is not None:
            gen["topP"] = top_p

        # T3: Gemini 2.5+ thinking. Gated on the resolved model being
        # flagged ``supports_reasoning=True`` (migration 045 records
        # which Gemini variants opt in via ``reasoning_strategy =
        # 'gemini_thinking_level'``). When enabled we add the
        # ``thinkingConfig`` block; ``includeThoughts`` is wired to
        # ``show_summary`` so the upstream API decides whether to
        # stream ``thought=true`` parts back. Skipped silently for
        # non-capable models so existing Gemini 1.5 / 2.0 calls are
        # byte-identical.
        reasoning_cfg = getattr(payload, "reasoning", None)
        if reasoning_cfg is not None and reasoning_cfg.mode != "off":
            from app.services.providers.model_registry import get_candidate
            effective_model = resolved_model or payload.model
            candidate = get_candidate(effective_model)
            if candidate is not None and candidate.supports_reasoning:
                gen["thinkingConfig"] = {
                    "thinkingLevel": _GEMINI_THINKING_LEVEL_MAP.get(
                        reasoning_cfg.mode,
                        _GEMINI_THINKING_LEVEL_MAP["auto"],
                    ),
                    "includeThoughts": bool(reasoning_cfg.show_summary),
                }

        if gen:
            body["generationConfig"] = gen

        return body

    async def _get_client(self):
        # Auth travels as the ``key`` query param (see call sites), so the
        # pooled client needs no per-request headers.
        return await get_provider_client(
            base_url=resolve_base_url(self.provider_name, GOOGLE_BASE_URL),
            timeout_connect=settings.provider_timeout_connect,
            timeout_read=settings.provider_timeout_read,
        )

    async def chat(
        self,
        payload,
        messages: list[Message],
        *,
        resolved_model: str | None = None,
        api_key: str | None = None,
    ) -> ProviderResponse:
        model = resolved_model or payload.model
        body = self._build_body(payload, messages, resolved_model=model)
        key = api_key or settings.google_api_key

        client = await self._get_client()
        resp = await call_with_retry(
            lambda: client.post(
                f"/models/{model}:generateContent",
                json=body,
                params={"key": key},
            ),
            provider_name=self.provider_name,
            max_retries=settings.provider_max_retries,
        )
        data = resp.json()

        return _parse_response(data)

    async def stream_chat(
        self,
        payload,
        messages: list[Message],
        *,
        resolved_model: str | None = None,
        api_key: str | None = None,
    ) -> AsyncIterator[ProviderStreamChunk]:
        model = resolved_model or payload.model
        body = self._build_body(payload, messages, resolved_model=model)
        key = api_key or settings.google_api_key

        client = await self._get_client()
        async with client.stream(
            "POST",
            f"/models/{model}:streamGenerateContent",
            json=body,
            params={"key": key, "alt": "sse"},
        ) as resp:
            # Map an HTTP error to a domain exception BEFORE any chunk is
            # yielded so the route layer's streaming-fallback loop sees a
            # GatewayError it can classify — a raw httpx.HTTPStatusError
            # fails its isinstance(_, GatewayError) gate and would skip
            # fallback. aread() loads the body so map_provider_error can
            # parse the error payload. Mirrors the openai/anthropic path.
            if resp.status_code >= 400:
                await resp.aread()
                try:
                    resp.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise map_provider_error(exc, self.provider_name) from exc
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if not data_str or data_str == "[DONE]":
                    continue
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                chunk = _parse_stream_chunk(data)
                if chunk is not None:
                    yield chunk
