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
        # P1 Item 4 — system is now a content-block array carrying a
        # cache_control breakpoint (prompt caching), not a plain string.
        assert body["system"] == [
            {
                "type": "text",
                "text": "You are helpful",
                "cache_control": {"type": "ephemeral"},
            }
        ]
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


# --- T5: extended-thinking round-trip + context budget ----------------------

from app.domain.routing import ModelCandidate  # noqa: E402
from app.schemas.public import ReasoningConfig  # noqa: E402
from app.services.providers.parsers import parse_anthropic_response  # noqa: E402


def test_serialize_message_replays_thinking_block_with_signature():
    """Assistant turn carrying reasoning_content + signature reconstructs the
    leading ``thinking`` block before the ``tool_use`` block (T5)."""
    msg = Message(
        role="assistant",
        content="Let me check the weather.",
        reasoning_content="The user asked about weather; call the tool.",
        reasoning_signature="sig-abc123",
        tool_calls=[{
            "id": "call_1", "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city":"Paris"}'},
        }],
    )
    from app.services.providers.anthropic_adapter import AnthropicAdapter
    serialized = AnthropicAdapter._serialize_message(msg)
    blocks = serialized["content"]
    assert blocks[0]["type"] == "thinking"
    assert blocks[0]["thinking"] == "The user asked about weather; call the tool."
    assert blocks[0]["signature"] == "sig-abc123"
    # text + tool_use follow the thinking block
    assert any(b["type"] == "tool_use" for b in blocks)


def test_serialize_message_no_thinking_block_without_signature():
    """Without a signature we cannot replay (Anthropic would reject); the
    thinking block is omitted rather than sent unsigned."""
    from app.services.providers.anthropic_adapter import AnthropicAdapter
    msg = Message(
        role="assistant",
        content="hi",
        reasoning_content="some thoughts",  # no signature
        tool_calls=[{"id": "c1", "type": "function",
                     "function": {"name": "f", "arguments": "{}"}}],
    )
    blocks = AnthropicAdapter._serialize_message(msg)["content"]
    assert all(b["type"] != "thinking" for b in blocks)


def test_parse_anthropic_response_extracts_signature():
    data = {
        "model": "claude-sonnet-4-20250514",
        "content": [
            {"type": "thinking", "thinking": "deep thought",
             "signature": "sig-xyz"},
            {"type": "text", "text": "The answer is 42."},
        ],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }
    r = parse_anthropic_response(data)
    assert r.reasoning_content == "deep thought"
    assert r.reasoning_signature == "sig-xyz"
    assert r.content == "The answer is 42."


def test_context_budget_caps_max_tokens_and_shrinks_budget(monkeypatch):
    """A high thinking budget on a small-window model is clamped so
    max_tokens fits the window and budget stays below max_tokens (T5)."""
    from app.services.providers import anthropic_adapter as aa

    def fake_get_candidate(model):
        return ModelCandidate(
            provider="anthropic", model=model,
            supports_reasoning=True, reasoning_strategy="anthropic_adaptive_thinking",
            max_context_tokens=8000,
        )

    monkeypatch.setattr(
        "app.services.providers.model_registry.get_candidate", fake_get_candidate
    )
    payload = _make_payload(model="claude-sonnet-4-20250514")
    payload.reasoning = ReasoningConfig(mode="high", show_summary=True)
    body = aa.AnthropicAdapter()._build_body(
        payload, [Message(role="user", content="hello")]
    )
    assert "thinking" in body
    # max_tokens must not exceed the window, and budget stays strictly below.
    assert body["max_tokens"] <= 8000
    assert body["thinking"]["budget_tokens"] < body["max_tokens"]


def test_context_budget_noop_when_window_unknown(monkeypatch):
    """When the model's window is unknown (0), the guard is skipped and the
    original budget+headroom bump stands."""
    from app.services.providers import anthropic_adapter as aa

    def fake_get_candidate(model):
        return ModelCandidate(
            provider="anthropic", model=model,
            supports_reasoning=True, reasoning_strategy="anthropic_adaptive_thinking",
            max_context_tokens=0,
        )

    monkeypatch.setattr(
        "app.services.providers.model_registry.get_candidate", fake_get_candidate
    )
    payload = _make_payload(model="claude-sonnet-4-20250514")
    payload.reasoning = ReasoningConfig(mode="high", show_summary=True)
    body = aa.AnthropicAdapter()._build_body(
        payload, [Message(role="user", content="hello")]
    )
    # budget high = 8192, padded to 8192+4096 since default max_tokens < budget
    assert body["thinking"]["budget_tokens"] == 8192
    assert body["max_tokens"] == 8192 + 4096


class TestAnthropicMultimodalContent:
    """OpenAI-format content arrays (vision turns from chat-edge) must
    become Anthropic ``image`` / ``text`` blocks — the native API
    rejects ``image_url`` parts passed through verbatim."""

    def test_image_url_data_url_becomes_image_block(self):
        from app.schemas.common import Message
        from app.services.providers.anthropic_adapter import AnthropicAdapter

        msg = Message(
            role="user",
            content=[
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,AAAA", "detail": "low"},
                },
                {"type": "text", "text": "what is this?"},
            ],
        )
        out = AnthropicAdapter._serialize_message(msg)
        assert out["role"] == "user"
        assert out["content"][0] == {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"},
        }
        assert out["content"][1] == {"type": "text", "text": "what is this?"}

    def test_http_url_becomes_url_source(self):
        from app.schemas.common import Message
        from app.services.providers.anthropic_adapter import AnthropicAdapter

        msg = Message(
            role="user",
            content=[{"type": "image_url", "image_url": {"url": "https://x/y.png"}}],
        )
        out = AnthropicAdapter._serialize_message(msg)
        assert out["content"][0] == {
            "type": "image",
            "source": {"type": "url", "url": "https://x/y.png"},
        }

    def test_plain_string_content_unchanged(self):
        from app.schemas.common import Message
        from app.services.providers.anthropic_adapter import AnthropicAdapter

        msg = Message(role="user", content="hi")
        assert AnthropicAdapter._serialize_message(msg) == {"role": "user", "content": "hi"}
