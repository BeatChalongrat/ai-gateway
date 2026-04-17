"""Z.AI (01.AI / Yi) chat completions adapter.

OpenAI-compatible API.
"""

from __future__ import annotations

from app.services.providers.openai_compatible_adapter import OpenAICompatibleAdapter

ZAI_BASE_URL = "https://api.01.ai/v1"


class ZAIAdapter(OpenAICompatibleAdapter):
    provider_name = "zai"
    base_url = ZAI_BASE_URL
    supports_streaming = True
