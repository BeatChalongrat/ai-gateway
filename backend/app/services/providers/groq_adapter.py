"""Groq adapter.

Groq exposes an OpenAI-compatible Chat Completions endpoint. This adapter
is a thin subclass of ``OpenAICompatibleAdapter`` — all
request/response/streaming logic is inherited.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, AsyncIterator

from app.core.config import settings
from app.domain.providers import ProviderResponse, ProviderStreamChunk
from app.services.providers.openai_compatible_adapter import OpenAICompatibleAdapter

if TYPE_CHECKING:
    from app.schemas.common import Message

GROQ_BASE_URL = "https://api.groq.com/openai/v1"


class GroqAdapter(OpenAICompatibleAdapter):
    provider_name = "groq"
    base_url = GROQ_BASE_URL

    async def chat(
        self,
        payload,
        messages: list[Message],
        *,
        resolved_model: str | None = None,
        api_key: str | None = None,
    ) -> ProviderResponse:
        return await super().chat(
            payload,
            messages,
            resolved_model=resolved_model,
            api_key=api_key or settings.groq_api_key,
        )

    async def stream_chat(
        self,
        payload,
        messages: list[Message],
        *,
        resolved_model: str | None = None,
        api_key: str | None = None,
    ) -> AsyncIterator[ProviderStreamChunk]:
        async for chunk in super().stream_chat(
            payload,
            messages,
            resolved_model=resolved_model,
            api_key=api_key or settings.groq_api_key,
        ):
            yield chunk
