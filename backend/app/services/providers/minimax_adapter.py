"""Minimax chat completions adapter.

OpenAI-compatible API — see ``openai_compatible_adapter.py`` for the
shared request/response and streaming implementation.
"""

from __future__ import annotations

from app.services.providers.openai_compatible_adapter import OpenAICompatibleAdapter

MINIMAX_BASE_URL = "https://api.minimax.chat/v1"


class MinimaxAdapter(OpenAICompatibleAdapter):
    provider_name = "minimax"
    base_url = MINIMAX_BASE_URL
    supports_streaming = True
