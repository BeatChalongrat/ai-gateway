"""P2-03: Anthropic adapter tests with mocked HTTP.

Verifies:
- Successful chat → ProviderResponse
- System message extracted to top-level param
- stop_reason mapped to finish_reason
- Usage mapped (input_tokens/output_tokens → prompt/completion)
- Auth headers (x-api-key, anthropic-version)
- Content blocks parsed to single string
"""

import json

import httpx
import pytest
import respx

from app.domain.providers import ProviderResponse
from app.schemas.common import Message
from app.schemas.public import ChatCompletionsRequest
from app.services.providers.anthropic_adapter import (
    ANTHROPIC_BASE_URL,
    AnthropicAdapter,
)


def _anthropic_response(
    text="Hello!",
    model="claude-3-5-sonnet-20241022",
    stop_reason="end_turn",
    input_tokens=10,
    output_tokens=5,
):
    return {
        "id": "msg_abc123",
        "type": "message",
        "model": model,
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    }


def _make_payload(model="auto", metadata=None):
    return ChatCompletionsRequest(
        model=model,
        messages=[Message(role="user", content="Hi")],
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


class TestAnthropicSuccess:
    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_provider_response(self):
        respx.post(f"{ANTHROPIC_BASE_URL}/messages").mock(
            return_value=httpx.Response(200, json=_anthropic_response())
        )
        adapter = AnthropicAdapter()
        result = await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")]
        )
        assert isinstance(result, ProviderResponse)

    @respx.mock
    @pytest.mark.asyncio
    async def test_content_extracted(self):
        respx.post(f"{ANTHROPIC_BASE_URL}/messages").mock(
            return_value=httpx.Response(
                200, json=_anthropic_response(text="World")
            )
        )
        adapter = AnthropicAdapter()
        result = await adapter.chat(
            _make_payload(), [Message(role="user", content="Hello")]
        )
        assert result.content == "World"

    @respx.mock
    @pytest.mark.asyncio
    async def test_model_from_response(self):
        respx.post(f"{ANTHROPIC_BASE_URL}/messages").mock(
            return_value=httpx.Response(
                200, json=_anthropic_response(model="claude-3-opus-20240229")
            )
        )
        adapter = AnthropicAdapter()
        result = await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")]
        )
        assert result.model == "claude-3-opus-20240229"

    @respx.mock
    @pytest.mark.asyncio
    async def test_usage_mapped(self):
        respx.post(f"{ANTHROPIC_BASE_URL}/messages").mock(
            return_value=httpx.Response(
                200,
                json=_anthropic_response(input_tokens=20, output_tokens=30),
            )
        )
        adapter = AnthropicAdapter()
        result = await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")]
        )
        assert result.usage.prompt_tokens == 20
        assert result.usage.completion_tokens == 30
        assert result.usage.total_tokens == 50

    @respx.mock
    @pytest.mark.asyncio
    async def test_raw_response_preserved(self):
        respx.post(f"{ANTHROPIC_BASE_URL}/messages").mock(
            return_value=httpx.Response(200, json=_anthropic_response())
        )
        adapter = AnthropicAdapter()
        result = await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")]
        )
        assert result.raw["id"] == "msg_abc123"


# ---------------------------------------------------------------------------
# Finish reason mapping
# ---------------------------------------------------------------------------


class TestAnthropicFinishReason:
    @respx.mock
    @pytest.mark.asyncio
    async def test_end_turn_maps_to_stop(self):
        respx.post(f"{ANTHROPIC_BASE_URL}/messages").mock(
            return_value=httpx.Response(
                200, json=_anthropic_response(stop_reason="end_turn")
            )
        )
        adapter = AnthropicAdapter()
        result = await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")]
        )
        assert result.finish_reason == "stop"

    @respx.mock
    @pytest.mark.asyncio
    async def test_max_tokens_maps_to_length(self):
        respx.post(f"{ANTHROPIC_BASE_URL}/messages").mock(
            return_value=httpx.Response(
                200, json=_anthropic_response(stop_reason="max_tokens")
            )
        )
        adapter = AnthropicAdapter()
        result = await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")]
        )
        assert result.finish_reason == "length"


# ---------------------------------------------------------------------------
# Request mapping
# ---------------------------------------------------------------------------


class TestAnthropicRequestMapping:
    @respx.mock
    @pytest.mark.asyncio
    async def test_system_message_extracted(self):
        route = respx.post(f"{ANTHROPIC_BASE_URL}/messages").mock(
            return_value=httpx.Response(200, json=_anthropic_response())
        )
        adapter = AnthropicAdapter()
        msgs = [
            Message(role="system", content="You are helpful"),
            Message(role="user", content="Hello"),
        ]
        await adapter.chat(_make_payload(), msgs)
        body = json.loads(route.calls[0].request.content)
        assert body["system"] == "You are helpful"
        assert len(body["messages"]) == 1
        assert body["messages"][0]["role"] == "user"

    @respx.mock
    @pytest.mark.asyncio
    async def test_no_system_message_omits_field(self):
        route = respx.post(f"{ANTHROPIC_BASE_URL}/messages").mock(
            return_value=httpx.Response(200, json=_anthropic_response())
        )
        adapter = AnthropicAdapter()
        msgs = [Message(role="user", content="Hello")]
        await adapter.chat(_make_payload(), msgs)
        body = json.loads(route.calls[0].request.content)
        assert "system" not in body

    @respx.mock
    @pytest.mark.asyncio
    async def test_max_tokens_default(self):
        route = respx.post(f"{ANTHROPIC_BASE_URL}/messages").mock(
            return_value=httpx.Response(200, json=_anthropic_response())
        )
        adapter = AnthropicAdapter()
        await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")]
        )
        body = json.loads(route.calls[0].request.content)
        assert body["max_tokens"] == 1024

    @respx.mock
    @pytest.mark.asyncio
    async def test_auth_headers(self):
        route = respx.post(f"{ANTHROPIC_BASE_URL}/messages").mock(
            return_value=httpx.Response(200, json=_anthropic_response())
        )
        adapter = AnthropicAdapter()
        await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")]
        )
        headers = route.calls[0].request.headers
        assert "x-api-key" in headers
        assert headers["anthropic-version"] == "2023-06-01"

    @respx.mock
    @pytest.mark.asyncio
    async def test_optional_params_forwarded(self):
        route = respx.post(f"{ANTHROPIC_BASE_URL}/messages").mock(
            return_value=httpx.Response(200, json=_anthropic_response())
        )
        adapter = AnthropicAdapter()
        payload = _make_payload(metadata={"temperature": 0.5, "max_tokens": 200})
        await adapter.chat(payload, [Message(role="user", content="Hi")])
        body = json.loads(route.calls[0].request.content)
        assert body["temperature"] == 0.5
        assert body["max_tokens"] == 200


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestAnthropicEdgeCases:
    @respx.mock
    @pytest.mark.asyncio
    async def test_multiple_content_blocks(self):
        data = _anthropic_response()
        data["content"] = [
            {"type": "text", "text": "Hello "},
            {"type": "text", "text": "World"},
        ]
        respx.post(f"{ANTHROPIC_BASE_URL}/messages").mock(
            return_value=httpx.Response(200, json=data)
        )
        adapter = AnthropicAdapter()
        result = await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")]
        )
        assert result.content == "Hello World"

    @respx.mock
    @pytest.mark.asyncio
    async def test_empty_content_blocks(self):
        data = _anthropic_response()
        data["content"] = []
        respx.post(f"{ANTHROPIC_BASE_URL}/messages").mock(
            return_value=httpx.Response(200, json=data)
        )
        adapter = AnthropicAdapter()
        result = await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")]
        )
        assert result.content == ""
