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
from typing import TYPE_CHECKING, AsyncIterator

from app.core.config import settings
from app.domain.providers import ProviderResponse, ProviderStreamChunk, ProviderUsage
from app.services.providers.base import BaseProviderAdapter
from app.services.providers.base_urls import resolve_base_url
from app.services.providers.error_handler import call_with_retry
from app.services.providers.http_client import create_provider_client

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
        {"role": _to_gemini_role(m.role), "parts": [{"text": m.content}]}
        for m in messages
    ]


def _parse_response(data: dict) -> ProviderResponse:
    candidates = data.get("candidates") or []
    content = ""
    finish_reason = "stop"
    if candidates:
        first = candidates[0]
        parts = (first.get("content") or {}).get("parts") or []
        content = "".join(p.get("text", "") for p in parts)
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
        usage = ProviderUsage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
        )

    return ProviderResponse(
        content=content,
        model=data.get("modelVersion", "") or "",
        finish_reason=finish_reason,
        usage=usage,
        raw=data,
    )


def _parse_stream_chunk(data: dict) -> ProviderStreamChunk | None:
    """Each SSE chunk from streamGenerateContent is a partial response."""
    candidates = data.get("candidates") or []
    delta = ""
    finish_reason: str | None = None
    if candidates:
        first = candidates[0]
        parts = (first.get("content") or {}).get("parts") or []
        delta = "".join(p.get("text", "") for p in parts)
        raw_finish = first.get("finishReason")
        if raw_finish:
            finish_reason = _FINISH_MAP.get(raw_finish, raw_finish.lower())

    usage = None
    meta = data.get("usageMetadata") or {}
    if meta:
        prompt = int(meta.get("promptTokenCount") or 0)
        completion = int(meta.get("candidatesTokenCount") or 0)
        total = int(meta.get("totalTokenCount") or (prompt + completion))
        usage = ProviderUsage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
        )

    if not delta and finish_reason is None and usage is None:
        return None

    return ProviderStreamChunk(
        delta=delta,
        finish_reason=finish_reason,
        model=data.get("modelVersion", "") or "",
        usage=usage,
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
        if gen:
            body["generationConfig"] = gen

        return body

    def _build_client(self):
        return create_provider_client(
            base_url=resolve_base_url(self.provider_name, GOOGLE_BASE_URL),
            timeout_connect=settings.provider_timeout_connect,
            timeout_read=settings.provider_timeout_read,
            headers={},
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

        async with self._build_client() as client:
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

        async with self._build_client() as client:
            async with client.stream(
                "POST",
                f"/models/{model}:streamGenerateContent",
                json=body,
                params={"key": key, "alt": "sse"},
            ) as resp:
                resp.raise_for_status()
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
