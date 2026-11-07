"""Sran AI (self-hosted box) chat completions adapter.

OpenAI-compatible API. Unlike the cloud providers there is no
hardcoded production URL — the endpoint is operator-supplied: the one on
the key itself (FIX85, a key added with its own endpoint) or the
provider-level ``system_settings`` row (admin UI), resolved at call time.
An unconfigured endpoint is a clean typed error, never an httpx call
against an empty base_url.
"""

from __future__ import annotations

from app.core.exceptions import ProviderUnavailableError
from app.services.providers.base_urls import effective_base_url
from app.services.providers.openai_compatible_adapter import OpenAICompatibleAdapter


class SranAIAdapter(OpenAICompatibleAdapter):
    provider_name = "sranai"
    base_url = ""  # self-hosted: per-key / DB override only, no cloud default
    supports_streaming = True

    async def _get_client(self, api_key: str | None = None):
        if not effective_base_url(self.provider_name, self.base_url, api_key):
            raise ProviderUnavailableError(
                "Sran AI endpoint is not configured — set the base URL "
                "on the admin /providers page.",
                details={"provider": self.provider_name},
            )
        return await super()._get_client(api_key)
