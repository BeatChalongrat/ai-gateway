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
from app.services.providers.http_client import create_provider_client
from app.services.providers.error_handler import call_with_retry
from app.services.providers.parsers import parse_openai_response

if TYPE_CHECKING:
    from app.schemas.common import Message

OPENAI_BASE_URL = "https://api.openai.com/v1"


class OpenAIAdapter(BaseProviderAdapter):
    provider_name = "openai"
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
            # Ask OpenAI to emit a final usage chunk so we can record
            # prompt/completion/total tokens for streaming requests.
            body["stream_options"] = {"include_usage": True}

        for param in ("temperature", "max_tokens", "top_p"):
            val = getattr(payload, param, None) or payload.metadata.get(param)
            if val is not None:
                body[param] = val

        return body

    async def chat(
        self, payload, messages: list[Message], *, resolved_model: str | None = None, api_key: str | None = None
    ) -> ProviderResponse:
        body = self._build_body(payload, messages, resolved_model=resolved_model)
        key = api_key or settings.openai_api_key

        client = create_provider_client(
            base_url=OPENAI_BASE_URL,
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
            base_url=OPENAI_BASE_URL,
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
        usage = ProviderUsage(
            prompt_tokens=int(usage_dict.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage_dict.get("completion_tokens", 0) or 0),
            total_tokens=int(usage_dict.get("total_tokens", 0) or 0),
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
    return ProviderStreamChunk(
        delta=delta.get("content", ""),
        finish_reason=choices[0].get("finish_reason"),
        model=data.get("model", ""),
        usage=usage,
    )
