"""Adapter tests for the 4 new OpenAI-compatible providers plus the
retrofitted OpenRouter streaming path.

For each adapter we cover four things:
1. ``chat()`` hits the documented base URL.
2. ``chat()`` sends an ``Authorization: Bearer <key>`` header.
3. ``stream_chat()`` yields the content deltas from a fake SSE body.
4. The adapter class advertises ``supports_streaming = True``.

HTTP traffic is mocked via ``respx`` — the same pattern used by the
existing OpenAI/Anthropic/OpenRouter adapter tests.

Plus one registry sanity test that pins every newly-registered model.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.schemas.common import Message
from app.schemas.public import ChatCompletionsRequest
from app.services.providers.kimi_adapter import KIMI_BASE_URL, KIMIAdapter
from app.services.providers.minimax_adapter import MINIMAX_BASE_URL, MinimaxAdapter
from app.services.providers.openrouter_adapter import (
    OPENROUTER_BASE_URL,
    OpenRouterAdapter,
)
from app.services.providers.qwen_adapter import QWEN_BASE_URL, QwenAdapter
from app.services.providers.zai_adapter import ZAI_BASE_URL, ZAIAdapter
from app.services.providers.model_registry import _MODEL_MAP, resolve_provider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _openai_shaped_response(content: str = "ok", model: str = "test-model") -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 3,
            "completion_tokens": 2,
            "total_tokens": 5,
        },
    }


def _payload(model: str) -> ChatCompletionsRequest:
    return ChatCompletionsRequest(
        model=model,
        messages=[Message(role="user", content="Hi")],
        metadata={},
    )


def _sse_body(deltas: list[str]) -> str:
    """Build a fake OpenAI-shaped SSE body with the given content deltas."""
    lines: list[str] = []
    for delta in deltas:
        lines.append(
            'data: {"id":"x","choices":[{"delta":{"content":"%s"},"finish_reason":null}],"model":"m"}'
            % delta
        )
    lines.append(
        'data: {"id":"x","choices":[{"delta":{},"finish_reason":"stop"}],"model":"m"}'
    )
    lines.append("data: [DONE]")
    return "\n\n".join(lines) + "\n\n"


# Parameter table: (adapter_class, base_url, sample_model)
_ADAPTERS = [
    (MinimaxAdapter, MINIMAX_BASE_URL, "abab6.5s-chat"),
    (QwenAdapter, QWEN_BASE_URL, "qwen-turbo"),
    (KIMIAdapter, KIMI_BASE_URL, "moonshot-v1-8k"),
    (ZAIAdapter, ZAI_BASE_URL, "yi-large"),
    (OpenRouterAdapter, OPENROUTER_BASE_URL, "openai/gpt-4o"),
]

_ADAPTER_IDS = ["minimax", "qwen", "kimi", "zai", "openrouter"]


# ---------------------------------------------------------------------------
# 1. Base URL
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("adapter_cls", "base_url", "model"),
    _ADAPTERS,
    ids=_ADAPTER_IDS,
)
class TestChatSendsCorrectBaseUrl:
    @respx.mock
    @pytest.mark.asyncio
    async def test_chat_sends_correct_base_url(self, adapter_cls, base_url, model):
        route = respx.post(f"{base_url}/chat/completions").mock(
            return_value=httpx.Response(200, json=_openai_shaped_response())
        )
        adapter = adapter_cls()
        await adapter.chat(
            _payload(model),
            [Message(role="user", content="Hi")],
            resolved_model=model,
            api_key="sk-test",
        )
        assert route.called
        assert str(route.calls[0].request.url).startswith(base_url)


# ---------------------------------------------------------------------------
# 2. Bearer auth
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("adapter_cls", "base_url", "model"),
    _ADAPTERS,
    ids=_ADAPTER_IDS,
)
class TestChatSendsBearerAuth:
    @respx.mock
    @pytest.mark.asyncio
    async def test_chat_sends_bearer_auth(self, adapter_cls, base_url, model):
        route = respx.post(f"{base_url}/chat/completions").mock(
            return_value=httpx.Response(200, json=_openai_shaped_response())
        )
        adapter = adapter_cls()
        await adapter.chat(
            _payload(model),
            [Message(role="user", content="Hi")],
            resolved_model=model,
            api_key="sk-secret-123",
        )
        auth = route.calls[0].request.headers.get("authorization")
        assert auth == "Bearer sk-secret-123"


# ---------------------------------------------------------------------------
# 3. Streaming yields content
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("adapter_cls", "base_url", "model"),
    _ADAPTERS,
    ids=_ADAPTER_IDS,
)
class TestStreamChatYieldsContent:
    @respx.mock
    @pytest.mark.asyncio
    async def test_stream_chat_yields_content(self, adapter_cls, base_url, model):
        body = _sse_body(["Hello", " world"])
        respx.post(f"{base_url}/chat/completions").mock(
            return_value=httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=body.encode(),
            )
        )
        adapter = adapter_cls()
        collected: list[str] = []
        async for chunk in adapter.stream_chat(
            _payload(model),
            [Message(role="user", content="Hi")],
            resolved_model=model,
            api_key="sk-test",
        ):
            if chunk.delta:
                collected.append(chunk.delta)
        assert collected == ["Hello", " world"]


# ---------------------------------------------------------------------------
# 4. supports_streaming flag
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("adapter_cls", "base_url", "model"),
    _ADAPTERS,
    ids=_ADAPTER_IDS,
)
class TestSupportsStreamingIsTrue:
    def test_supports_streaming_is_true(self, adapter_cls, base_url, model):
        assert adapter_cls.supports_streaming is True


# ---------------------------------------------------------------------------
# 5. Model registry pins the new models
# ---------------------------------------------------------------------------


class TestModelRegistryIncludesAllNewModels:
    def test_model_registry_includes_all_new_models(self):
        expected = {
            # Minimax
            "abab6.5s-chat": "minimax",
            "abab5.5-chat": "minimax",
            # Qwen
            "qwen-turbo": "qwen",
            "qwen-plus": "qwen",
            "qwen-max": "qwen",
            # KIMI
            "moonshot-v1-8k": "kimi",
            "moonshot-v1-32k": "kimi",
            "moonshot-v1-128k": "kimi",
            # Z.AI
            "yi-large": "zai",
            "yi-medium": "zai",
        }
        for model, provider in expected.items():
            assert model in _MODEL_MAP, f"{model} missing from registry"
            assert _MODEL_MAP[model].provider == provider
            assert resolve_provider(model) == provider
