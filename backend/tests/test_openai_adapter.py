"""P2-02: OpenAI adapter tests with mocked HTTP.

Verifies:
- Successful chat completion → ProviderResponse
- Request body mapping (model, messages, optional params)
- Response parsing (content, model, finish_reason, usage)
- Authorization header sent
- Raw response preserved
"""

import pytest
import respx
import httpx

from app.domain.providers import ProviderResponse, ProviderUsage
from app.schemas.common import Message
from app.schemas.public import ChatCompletionsRequest
from app.services.providers.openai_adapter import OpenAIAdapter, OPENAI_BASE_URL


def _openai_response(
    content="Hello!",
    model="gpt-4o-mini",
    finish_reason="stop",
    prompt_tokens=10,
    completion_tokens=5,
):
    return {
        "id": "chatcmpl-abc123",
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


def _make_payload(model="gpt-4o-mini", metadata=None):
    return ChatCompletionsRequest(
        model=model,
        messages=[Message(role="user", content="Hi")],
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


class TestOpenAIAdapterSuccess:
    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_provider_response(self):
        respx.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_openai_response())
        )
        adapter = OpenAIAdapter()
        result = await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")]
        )
        assert isinstance(result, ProviderResponse)

    @respx.mock
    @pytest.mark.asyncio
    async def test_content_extracted(self):
        respx.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(
                200, json=_openai_response(content="World")
            )
        )
        adapter = OpenAIAdapter()
        result = await adapter.chat(
            _make_payload(), [Message(role="user", content="Hello")]
        )
        assert result.content == "World"

    @respx.mock
    @pytest.mark.asyncio
    async def test_model_from_response(self):
        respx.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(
                200, json=_openai_response(model="gpt-4o-2024-08-06")
            )
        )
        adapter = OpenAIAdapter()
        result = await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")]
        )
        assert result.model == "gpt-4o-2024-08-06"

    @respx.mock
    @pytest.mark.asyncio
    async def test_finish_reason(self):
        respx.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(
                200, json=_openai_response(finish_reason="length")
            )
        )
        adapter = OpenAIAdapter()
        result = await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")]
        )
        assert result.finish_reason == "length"

    @respx.mock
    @pytest.mark.asyncio
    async def test_usage_extracted(self):
        respx.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json=_openai_response(prompt_tokens=20, completion_tokens=30),
            )
        )
        adapter = OpenAIAdapter()
        result = await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")]
        )
        assert result.usage is not None
        assert result.usage.prompt_tokens == 20
        assert result.usage.completion_tokens == 30
        assert result.usage.total_tokens == 50

    @respx.mock
    @pytest.mark.asyncio
    async def test_raw_response_preserved(self):
        raw = _openai_response()
        respx.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=raw)
        )
        adapter = OpenAIAdapter()
        result = await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")]
        )
        assert result.raw["id"] == "chatcmpl-abc123"


# ---------------------------------------------------------------------------
# Request mapping
# ---------------------------------------------------------------------------


class TestOpenAIRequestMapping:
    @respx.mock
    @pytest.mark.asyncio
    async def test_model_sent_in_body(self):
        route = respx.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_openai_response())
        )
        adapter = OpenAIAdapter()
        await adapter.chat(
            _make_payload(model="gpt-4o"),
            [Message(role="user", content="test")],
        )
        body = route.calls[0].request.content
        import json

        parsed = json.loads(body)
        assert parsed["model"] == "gpt-4o"

    @respx.mock
    @pytest.mark.asyncio
    async def test_resolved_model_used_over_payload(self):
        route = respx.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_openai_response())
        )
        adapter = OpenAIAdapter()
        await adapter.chat(
            _make_payload(model="auto"),
            [Message(role="user", content="test")],
            resolved_model="gpt-4o-mini",
        )
        import json

        parsed = json.loads(route.calls[0].request.content)
        assert parsed["model"] == "gpt-4o-mini"

    @respx.mock
    @pytest.mark.asyncio
    async def test_messages_mapped(self):
        route = respx.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_openai_response())
        )
        adapter = OpenAIAdapter()
        msgs = [
            Message(role="system", content="You are helpful"),
            Message(role="user", content="Hello"),
        ]
        await adapter.chat(_make_payload(), msgs)
        import json

        parsed = json.loads(route.calls[0].request.content)
        assert len(parsed["messages"]) == 2
        assert parsed["messages"][0]["role"] == "system"
        assert parsed["messages"][1]["content"] == "Hello"

    @respx.mock
    @pytest.mark.asyncio
    async def test_optional_params_forwarded(self):
        route = respx.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_openai_response())
        )
        adapter = OpenAIAdapter()
        payload = _make_payload(metadata={"temperature": 0.7, "max_tokens": 100})
        await adapter.chat(payload, [Message(role="user", content="Hi")])
        import json

        parsed = json.loads(route.calls[0].request.content)
        assert parsed["temperature"] == 0.7
        assert parsed["max_tokens"] == 100

    @respx.mock
    @pytest.mark.asyncio
    async def test_auth_header_sent(self):
        route = respx.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_openai_response())
        )
        adapter = OpenAIAdapter()
        await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")]
        )
        auth = route.calls[0].request.headers.get("authorization")
        assert auth is not None
        assert auth.startswith("Bearer ")


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestOpenAIEdgeCases:
    @respx.mock
    @pytest.mark.asyncio
    async def test_missing_usage_in_response(self):
        data = _openai_response()
        del data["usage"]
        respx.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=data)
        )
        adapter = OpenAIAdapter()
        result = await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")]
        )
        assert result.usage is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_empty_content(self):
        data = _openai_response(content="")
        respx.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=data)
        )
        adapter = OpenAIAdapter()
        result = await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")]
        )
        assert result.content == ""
