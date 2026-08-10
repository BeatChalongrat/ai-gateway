"""Sran AI (self-hosted box) chat completions adapter.

OpenAI-compatible API. Unlike the cloud providers there is no
hardcoded production URL — the endpoint is operator-supplied and
resolved at call time from ``system_settings`` (admin UI) or the
``PROVIDER_BASE_URL_OVERRIDES`` env map. An unconfigured endpoint is a
clean typed error, never an httpx call against an empty base_url.
"""

from __future__ import annotations

from app.core.exceptions import ProviderUnavailableError
from app.services.providers.base_urls import resolve_base_url
from app.services.providers.openai_compatible_adapter import OpenAICompatibleAdapter


class SranAIAdapter(OpenAICompatibleAdapter):
    provider_name = "sranai"
    base_url = ""  # self-hosted: DB/env override only, no cloud default
    supports_streaming = True

    async def _get_client(self):
        if not resolve_base_url(self.provider_name, self.base_url):
            raise ProviderUnavailableError(
                "Sran AI endpoint is not configured — set the base URL "
                "on the admin /providers page (or PROVIDER_BASE_URL_OVERRIDES).",
                details={"provider": self.provider_name},
            )
        return await super()._get_client()
