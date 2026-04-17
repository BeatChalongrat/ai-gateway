"""KIMI (Moonshot AI) chat completions adapter.

OpenAI-compatible API.
"""

from __future__ import annotations

from app.services.providers.openai_compatible_adapter import OpenAICompatibleAdapter

KIMI_BASE_URL = "https://api.moonshot.cn/v1"


class KIMIAdapter(OpenAICompatibleAdapter):
    provider_name = "kimi"
    base_url = KIMI_BASE_URL
    supports_streaming = True
