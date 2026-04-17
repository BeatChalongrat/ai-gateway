"""Qwen (Alibaba DashScope) chat completions adapter.

Uses DashScope's OpenAI-compatible endpoint (``compatible-mode``).
"""

from __future__ import annotations

from app.services.providers.openai_compatible_adapter import OpenAICompatibleAdapter

QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class QwenAdapter(OpenAICompatibleAdapter):
    provider_name = "qwen"
    base_url = QWEN_BASE_URL
    supports_streaming = True
