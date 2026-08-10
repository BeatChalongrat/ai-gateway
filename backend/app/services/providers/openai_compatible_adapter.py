"""Shared base class for OpenAI-compatible providers.

Many providers expose an API that is byte-for-byte compatible with
OpenAI's Chat Completions endpoint — Minimax, Qwen (DashScope
compatible-mode), KIMI / Moonshot, Z.AI / 01.AI, etc. This class
implements the full request/response and SSE streaming flow so that
each concrete adapter only has to override ``base_url`` and
``provider_name``.

Adapters that need extra headers (e.g. OpenRouter's ``HTTP-Referer``)
can subclass and override ``_extra_headers()``.

Design notes:
- ``openai_adapter.OpenAIAdapter`` is deliberately left untouched so
  the 35+ existing OpenAI tests continue to pass without modification.
- SSE parsing is reused from ``openai_adapter._parse_sse_line`` since
  the wire format is identical across all of these providers.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, AsyncIterator

import httpx

from app.core.config import settings
from app.domain.providers import ProviderResponse, ProviderStreamChunk
from app.services.providers.base import BaseProviderAdapter
from app.services.providers.base_urls import resolve_base_url
from app.services.providers.error_handler import call_with_retry, map_provider_error
from app.services.providers.http_client import get_provider_client
from app.services.providers.openai_adapter import (
    _REASONING_EFFORT_MAP,
    _parse_sse_line,
)
from app.services.providers.parsers import parse_openai_response

if TYPE_CHECKING:
    from app.schemas.common import Message


class OpenAICompatibleAdapter(BaseProviderAdapter):
    """Concrete-enough base for any provider that mirrors OpenAI's
    Chat Completions API. Subclasses must set ``base_url`` and
    ``provider_name``.
    """

    base_url: str = ""
    provider_name: str = ""
    supports_streaming: bool = True

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------

    def _extra_headers(self) -> dict[str, str]:
        """Extra headers merged on top of the default Authorization."""
        return {}

    # ------------------------------------------------------------------
    # Request body
    # ------------------------------------------------------------------

    def _build_body(
        self,
        payload,
        messages,
        *,
        stream: bool = False,
        resolved_model: str | None = None,
    ) -> dict:
        model = resolved_model or payload.model
        body: dict = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if stream:
            body["stream"] = True

        for param in ("temperature", "max_tokens", "top_p"):
            val = getattr(payload, param, None) or payload.metadata.get(param)
            if val is not None:
                body[param] = val

        # T3: forward ``reasoning_effort`` for reasoning-capable models
        # served over the OpenAI-compatible wire (covers DeepSeek-R1
        # and Groq R1-distill today; future R1-style models pick this
        # up automatically when their registry row is flagged
        # ``supports_reasoning=True`` with strategy
        # ``openai_compatible_reasoning``). DeepSeek/Groq actually
        # surface reasoning even WITHOUT this knob (their R1 models
        # always emit ``reasoning_content``); sending the param is a
        # forward-compat hint for providers that gate on it. Non-
        # reasoning rows skip this branch and stay byte-identical.
        # Unlike the native OpenAI adapter we do NOT strip temperature
        # / top_p or swap ``max_tokens`` → ``max_completion_tokens``
        # — DeepSeek-R1 / Groq accept the legacy sampling knobs.
        reasoning_cfg = getattr(payload, "reasoning", None)
        if reasoning_cfg is not None and reasoning_cfg.mode != "off":
            from app.services.providers.model_registry import get_candidate
            candidate = get_candidate(model)
            if candidate is not None and candidate.supports_reasoning:
                body["reasoning_effort"] = _REASONING_EFFORT_MAP.get(
                    reasoning_cfg.mode, _REASONING_EFFORT_MAP["auto"]
                )

        return body

    def _request_headers(self, api_key: str | None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {api_key or ''}"}
        headers.update(self._extra_headers())
        return headers

    async def _get_client(self):
        # Resolve at call time so PROVIDER_BASE_URL_OVERRIDES can steer
        # traffic to the perf mock without a code change. Falls through
        # to the subclass's hardcoded ``base_url`` when no override set.
        effective_base_url = resolve_base_url(self.provider_name, self.base_url)
        return await get_provider_client(
            base_url=effective_base_url,
            timeout_connect=settings.provider_timeout_connect,
            timeout_read=settings.provider_timeout_read,
            provider_name=self.provider_name,
        )

    # ------------------------------------------------------------------
    # Non-streaming
    # ------------------------------------------------------------------

    async def chat(
        self,
        payload,
        messages: list[Message],
        *,
        resolved_model: str | None = None,
        api_key: str | None = None,
    ) -> ProviderResponse:
        body = self._build_body(payload, messages, resolved_model=resolved_model)

        client = await self._get_client()
        headers = self._request_headers(api_key)
        resp = await call_with_retry(
            lambda: client.post("/chat/completions", json=body, headers=headers),
            provider_name=self.provider_name,
            max_retries=settings.provider_max_retries,
        )
        data = resp.json()

        return parse_openai_response(data)

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    async def stream_chat(
        self,
        payload,
        messages: list[Message],
        *,
        resolved_model: str | None = None,
        api_key: str | None = None,
    ) -> AsyncIterator[ProviderStreamChunk]:
        body = self._build_body(
            payload, messages, stream=True, resolved_model=resolved_model
        )

        client = await self._get_client()
        headers = self._request_headers(api_key)
        async with client.stream(
            "POST", "/chat/completions", json=body, headers=headers
        ) as resp:
            # Map an HTTP error to a domain exception BEFORE any chunk is
            # yielded so the route layer's streaming-fallback loop sees a
            # GatewayError it can classify — a raw httpx.HTTPStatusError
            # fails its isinstance(_, GatewayError) gate and would skip
            # fallback. aread() loads the body so map_provider_error can
            # parse the error payload. Mirrors the openai/anthropic stream
            # path. Covers all OpenAI-compatible providers that subclass
            # this adapter (groq, xai, deepseek, qwen, kimi, zai, minimax).
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
