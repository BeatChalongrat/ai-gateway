"""Mistral adapter (pending).

# TODO(sran-provider-adapters): Full implementation pending
# Schema docs: https://docs.mistral.ai/api/
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.exceptions import ProviderNotImplementedError
from app.domain.providers import ProviderResponse
from app.services.providers.base import BaseProviderAdapter

if TYPE_CHECKING:
    from app.schemas.common import Message


class MistralAdapter(BaseProviderAdapter):
    provider_name = "mistral"
    supports_streaming = False

    async def chat(
        self,
        payload,
        messages: list[Message],
        *,
        resolved_model: str | None = None,
        api_key: str | None = None,
    ) -> ProviderResponse:
        raise ProviderNotImplementedError("Mistral adapter implementation pending")
