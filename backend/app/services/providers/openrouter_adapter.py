"""OpenRouter adapter.

OpenRouter exposes an OpenAI-compatible API with a different base URL
and optional extra headers.  Model names are passed through directly
(e.g. ``openai/gpt-4o``, ``anthropic/claude-3.5-sonnet``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, AsyncIterator

import httpx

from app.core.config import settings
from app.domain.providers import ProviderResponse, ProviderStreamChunk
from app.services.providers.base import BaseProviderAdapter
from app.services.providers.base_urls import resolve_base_url
from app.services.providers.http_client import get_provider_client
from app.services.providers.error_handler import call_with_retry, map_provider_error
from app.services.providers.openai_adapter import _parse_sse_line
from app.services.providers.parsers import parse_openai_response

if TYPE_CHECKING:
    from app.schemas.common import Message

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Reasoning mode → OpenRouter ``reasoning.effort`` value. ``auto``
# collapses to medium (same posture the audit recommended).
_OPENROUTER_REASONING_EFFORT: dict[str, str] = {
    "auto": "medium",
    "low": "low",
    "medium": "medium",
    "high": "high",
}


class OpenRouterAdapter(BaseProviderAdapter):
    provider_name = "openrouter"
    supports_streaming = True

    def _build_body(
        self, payload, messages, *, stream: bool = False, resolved_model: str | None = None
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

        # T3: OpenRouter reasoning. The OR API accepts both the legacy
        # ``include_reasoning: true`` flag AND the newer
        # ``reasoning: {effort: low|medium|high}`` shape; we send
        # both so upstream-side rollouts of either still apply our
        # mode. Gated on the resolved model being flagged
        # ``supports_reasoning=True`` (migration 045) — non-reasoning
        # OR models are byte-identical to the pre-T3 body.
        # Response-side parsing (``message.reasoning_content`` /
        # ``delta.reasoning_content``) already comes for free via the
        # shared OpenAI-compatible parsers updated in T2.
        reasoning_cfg = getattr(payload, "reasoning", None)
        if reasoning_cfg is not None and reasoning_cfg.mode != "off":
            from app.services.providers.model_registry import get_candidate
            candidate = get_candidate(model)
            if candidate is not None and candidate.supports_reasoning:
                body["include_reasoning"] = True
                effort = _OPENROUTER_REASONING_EFFORT.get(
                    reasoning_cfg.mode, _OPENROUTER_REASONING_EFFORT["auto"]
                )
                body["reasoning"] = {"effort": effort}

        return body

    def _request_headers(self, api_key: str | None) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key or settings.openrouter_api_key}",
            "HTTP-Referer": settings.app_name,
        }

    async def _get_client(self):
        return await get_provider_client(
            base_url=resolve_base_url(self.provider_name, OPENROUTER_BASE_URL),
            timeout_connect=settings.provider_timeout_connect,
            timeout_read=settings.provider_timeout_read,
        )

    async def chat(
        self, payload, messages: list[Message], *, resolved_model: str | None = None, api_key: str | None = None
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
            # parse the error payload. Mirrors the openai/anthropic path.
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
