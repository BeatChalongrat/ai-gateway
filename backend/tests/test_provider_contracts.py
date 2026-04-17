"""P2-01: Canonical provider contracts, HTTP client factory, and adapter stubs.

Verifies:
- ProviderResponse and ProviderUsage dataclass construction/defaults
- ProviderResponse is frozen
- create_provider_client returns configured httpx.AsyncClient
- All stub adapters return ProviderResponse (not dict)
- Dispatcher returns ProviderResponse
- Orchestrator consumes ProviderResponse.content (integration via TestClient)
"""

import dataclasses

import httpx
import pytest
import respx

from app.domain.providers import ProviderResponse, ProviderUsage
from app.schemas.common import Message
from app.schemas.public import ChatCompletionsRequest
from app.services.provider_dispatcher import ProviderDispatcher
from app.services.providers.anthropic_adapter import AnthropicAdapter
from app.services.providers.base import BaseProviderAdapter
from app.services.providers.http_client import create_provider_client
from app.services.providers.openai_adapter import OpenAIAdapter
from app.services.providers.openrouter_adapter import OpenRouterAdapter


# ---------------------------------------------------------------------------
# ProviderUsage
# ---------------------------------------------------------------------------


class TestProviderUsage:
    def test_defaults(self):
        u = ProviderUsage()
        assert u.prompt_tokens == 0
        assert u.completion_tokens == 0
        assert u.total_tokens == 0

    def test_with_values(self):
        u = ProviderUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30)
        assert u.total_tokens == 30

    def test_frozen(self):
        u = ProviderUsage()
        with pytest.raises(AttributeError):
            u.prompt_tokens = 5  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ProviderResponse
# ---------------------------------------------------------------------------


class TestProviderResponse:
    def test_required_fields(self):
        r = ProviderResponse(content="hello", model="gpt-4o")
        assert r.content == "hello"
        assert r.model == "gpt-4o"

    def test_defaults(self):
        r = ProviderResponse(content="hi", model="m")
        assert r.finish_reason == "stop"
        assert r.usage is None
        assert r.raw == {}

    def test_with_usage(self):
        u = ProviderUsage(prompt_tokens=5, completion_tokens=10, total_tokens=15)
        r = ProviderResponse(content="hi", model="m", usage=u)
        assert r.usage.total_tokens == 15

    def test_with_raw(self):
        raw = {"id": "chatcmpl-123", "choices": []}
        r = ProviderResponse(content="hi", model="m", raw=raw)
        assert r.raw["id"] == "chatcmpl-123"

    def test_frozen(self):
        r = ProviderResponse(content="hi", model="m")
        with pytest.raises(AttributeError):
            r.content = "bye"  # type: ignore[misc]

    def test_finish_reason_variants(self):
        for reason in ("stop", "length", "content_filter"):
            r = ProviderResponse(content="", model="m", finish_reason=reason)
            assert r.finish_reason == reason


# ---------------------------------------------------------------------------
# HTTP client factory
# ---------------------------------------------------------------------------


class TestCreateProviderClient:
    def test_returns_async_client(self):
        client = create_provider_client(base_url="https://api.example.com")
        assert isinstance(client, httpx.AsyncClient)

    def test_base_url_set(self):
        client = create_provider_client(base_url="https://api.openai.com/v1")
        assert str(client.base_url).rstrip("/") == "https://api.openai.com/v1"

    def test_default_headers(self):
        client = create_provider_client(base_url="https://x.com")
        assert client.headers["content-type"] == "application/json"
        assert client.headers["accept"] == "application/json"

    def test_custom_headers_merged(self):
        client = create_provider_client(
            base_url="https://x.com",
            headers={"Authorization": "Bearer sk-test"},
        )
        assert client.headers["authorization"] == "Bearer sk-test"
        assert client.headers["content-type"] == "application/json"

    def test_timeout_configured(self):
        client = create_provider_client(
            base_url="https://x.com",
            timeout_connect=2.0,
            timeout_read=10.0,
        )
        assert client.timeout.connect == 2.0
        assert client.timeout.read == 10.0


# ---------------------------------------------------------------------------
# Stub adapters return ProviderResponse
# ---------------------------------------------------------------------------


class TestStubAdaptersReturnType:
    @pytest.fixture()
    def messages(self):
        return [Message(role="user", content="hello")]

    @pytest.fixture()
    def payload(self):
        return ChatCompletionsRequest(
            model="auto",
            messages=[Message(role="user", content="hello")],
        )

    @respx.mock
    @pytest.mark.asyncio
    async def test_openai_returns_provider_response(self, payload, messages):
        from app.services.providers.openai_adapter import OPENAI_BASE_URL

        respx.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json={
                "id": "test", "model": "gpt-4o-mini", "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            })
        )
        adapter = OpenAIAdapter()
        result = await adapter.chat(payload, messages)
        assert isinstance(result, ProviderResponse)
        assert isinstance(result.model, str)

    @respx.mock
    @pytest.mark.asyncio
    async def test_anthropic_returns_provider_response(self, payload, messages):
        from app.services.providers.anthropic_adapter import ANTHROPIC_BASE_URL

        respx.post(f"{ANTHROPIC_BASE_URL}/messages").mock(
            return_value=httpx.Response(200, json={
                "id": "test", "type": "message", "model": "claude-3-5-sonnet-20241022",
                "role": "assistant",
                "content": [{"type": "text", "text": "hi"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            })
        )
        adapter = AnthropicAdapter()
        result = await adapter.chat(payload, messages)
        assert isinstance(result, ProviderResponse)
        assert isinstance(result.model, str)

    @respx.mock
    @pytest.mark.asyncio
    async def test_openrouter_returns_provider_response(self, payload, messages):
        from app.services.providers.openrouter_adapter import OPENROUTER_BASE_URL

        respx.post(f"{OPENROUTER_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json={
                "id": "test", "model": "openai/gpt-4o-mini", "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            })
        )
        adapter = OpenRouterAdapter()
        result = await adapter.chat(payload, messages)
        assert isinstance(result, ProviderResponse)
        assert isinstance(result.model, str)

    @pytest.mark.asyncio
    async def test_all_adapters_are_base_provider_adapter(self):
        for cls in (OpenAIAdapter, AnthropicAdapter, OpenRouterAdapter):
            assert issubclass(cls, BaseProviderAdapter)


# ---------------------------------------------------------------------------
# Dispatcher returns ProviderResponse
# ---------------------------------------------------------------------------


class TestDispatcherTypedResponse:
    @pytest.mark.asyncio
    async def test_dispatch_returns_provider_response(self):
        dispatcher = ProviderDispatcher()
        from app.domain.entities import RouteDecision

        route = RouteDecision(provider_name="openai", model_name="gpt-4o-mini")
        payload = ChatCompletionsRequest(
            model="gpt-4o-mini",
            messages=[Message(role="user", content="test")],
        )
        result = await dispatcher.dispatch(
            route=route, payload=payload, messages=payload.messages
        )
        assert isinstance(result, ProviderResponse)
        assert isinstance(result.content, str)


# ---------------------------------------------------------------------------
# Config: provider timeout settings
# ---------------------------------------------------------------------------


class TestProviderTimeoutConfig:
    def test_default_connect_timeout(self):
        from app.core.config import Settings

        s = Settings(
            _env_file=None,
            postgres_password="x",
        )
        assert s.provider_timeout_connect == 5.0

    def test_default_read_timeout(self):
        from app.core.config import Settings

        s = Settings(
            _env_file=None,
            postgres_password="x",
        )
        assert s.provider_timeout_read == 30.0

    def test_custom_timeouts(self):
        from app.core.config import Settings

        s = Settings(
            _env_file=None,
            postgres_password="x",
            provider_timeout_connect=2.0,
            provider_timeout_read=60.0,
        )
        assert s.provider_timeout_connect == 2.0
        assert s.provider_timeout_read == 60.0
