"""P2-04: OpenRouter adapter tests with mocked HTTP.

Verifies:
- Successful chat → ProviderResponse (OpenAI-compatible parsing)
- OpenRouter base URL used
- Auth header and HTTP-Referer header sent
- Model passthrough
"""

import json

import httpx
import pytest
import respx

from app.domain.providers import ProviderResponse
from app.schemas.common import Message
from app.schemas.public import ChatCompletionsRequest
from app.services.providers.openrouter_adapter import (
    OPENROUTER_BASE_URL,
    OpenRouterAdapter,
)


def _openrouter_response(
    content="Hello!",
    model="openai/gpt-4o-mini",
    finish_reason="stop",
    prompt_tokens=10,
    completion_tokens=5,
):
    return {
        "id": "gen-abc123",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _make_payload(model="auto", metadata=None):
    return ChatCompletionsRequest(
        model=model,
        messages=[Message(role="user", content="Hi")],
        metadata=metadata or {},
    )


class TestOpenRouterSuccess:
    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_provider_response(self):
        respx.post(f"{OPENROUTER_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_openrouter_response())
        )
        adapter = OpenRouterAdapter()
        result = await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")]
        )
        assert isinstance(result, ProviderResponse)
        assert result.content == "Hello!"

    @respx.mock
    @pytest.mark.asyncio
    async def test_usage_extracted(self):
        respx.post(f"{OPENROUTER_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(
                200, json=_openrouter_response(prompt_tokens=20, completion_tokens=10)
            )
        )
        adapter = OpenRouterAdapter()
        result = await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")]
        )
        assert result.usage.total_tokens == 30

    @respx.mock
    @pytest.mark.asyncio
    async def test_model_from_response(self):
        respx.post(f"{OPENROUTER_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(
                200, json=_openrouter_response(model="anthropic/claude-3.5-sonnet")
            )
        )
        adapter = OpenRouterAdapter()
        result = await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")]
        )
        assert result.model == "anthropic/claude-3.5-sonnet"


class TestOpenRouterRequestMapping:
    @respx.mock
    @pytest.mark.asyncio
    async def test_model_passthrough(self):
        route = respx.post(f"{OPENROUTER_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_openrouter_response())
        )
        adapter = OpenRouterAdapter()
        await adapter.chat(
            _make_payload(model="meta-llama/llama-3-70b"),
            [Message(role="user", content="Hi")],
        )
        body = json.loads(route.calls[0].request.content)
        assert body["model"] == "meta-llama/llama-3-70b"

    @respx.mock
    @pytest.mark.asyncio
    async def test_resolved_model_used_over_payload(self):
        route = respx.post(f"{OPENROUTER_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_openrouter_response())
        )
        adapter = OpenRouterAdapter()
        await adapter.chat(
            _make_payload(model="auto"),
            [Message(role="user", content="Hi")],
            resolved_model="openai/gpt-4o-mini",
        )
        body = json.loads(route.calls[0].request.content)
        assert body["model"] == "openai/gpt-4o-mini"

    @respx.mock
    @pytest.mark.asyncio
    async def test_auth_header_sent(self):
        route = respx.post(f"{OPENROUTER_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_openrouter_response())
        )
        adapter = OpenRouterAdapter()
        await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")]
        )
        headers = route.calls[0].request.headers
        assert "authorization" in headers
        assert headers["authorization"].startswith("Bearer ")

    @respx.mock
    @pytest.mark.asyncio
    async def test_referer_header_sent(self):
        route = respx.post(f"{OPENROUTER_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_openrouter_response())
        )
        adapter = OpenRouterAdapter()
        await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")]
        )
        headers = route.calls[0].request.headers
        assert "http-referer" in headers
