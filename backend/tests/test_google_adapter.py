"""Google AI (Gemini) adapter tests with mocked HTTP.

Verifies:
- Successful chat → ProviderResponse with normalized finish_reason and usage
- System messages are lifted into systemInstruction
- Role mapping: assistant → model
- Streaming SSE yields ProviderStreamChunk deltas
"""

import json

import httpx
import pytest
import respx

from app.domain.providers import ProviderResponse, ProviderStreamChunk
from app.schemas.common import Message
from app.schemas.public import ChatCompletionsRequest
from app.services.providers.google_adapter import (
    GOOGLE_BASE_URL,
    GoogleAIAdapter,
    _build_contents,
    _split_system,
)


def _gemini_response(text="Hello!", finish="STOP", prompt_tokens=10, completion_tokens=5):
    return {
        "candidates": [
            {
                "content": {"role": "model", "parts": [{"text": text}]},
                "finishReason": finish,
            }
        ],
        "modelVersion": "gemini-1.5-flash",
        "usageMetadata": {
            "promptTokenCount": prompt_tokens,
            "candidatesTokenCount": completion_tokens,
            "totalTokenCount": prompt_tokens + completion_tokens,
        },
    }


def _make_payload(model="gemini-1.5-flash", metadata=None):
    return ChatCompletionsRequest(
        model=model,
        messages=[Message(role="user", content="Hi")],
        metadata=metadata or {},
    )


class TestGoogleAISuccess:
    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_provider_response(self):
        respx.post(f"{GOOGLE_BASE_URL}/models/gemini-1.5-flash:generateContent").mock(
            return_value=httpx.Response(200, json=_gemini_response())
        )
        adapter = GoogleAIAdapter()
        result = await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")], api_key="fake-key"
        )
        assert isinstance(result, ProviderResponse)
        assert result.content == "Hello!"
        assert result.finish_reason == "stop"

    @respx.mock
    @pytest.mark.asyncio
    async def test_usage_extracted(self):
        respx.post(f"{GOOGLE_BASE_URL}/models/gemini-1.5-flash:generateContent").mock(
            return_value=httpx.Response(
                200, json=_gemini_response(prompt_tokens=20, completion_tokens=10)
            )
        )
        adapter = GoogleAIAdapter()
        result = await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")], api_key="fake-key"
        )
        assert result.usage is not None
        assert result.usage.prompt_tokens == 20
        assert result.usage.completion_tokens == 10
        assert result.usage.total_tokens == 30

    @respx.mock
    @pytest.mark.asyncio
    async def test_finish_reason_mapping(self):
        respx.post(f"{GOOGLE_BASE_URL}/models/gemini-1.5-flash:generateContent").mock(
            return_value=httpx.Response(200, json=_gemini_response(finish="MAX_TOKENS"))
        )
        adapter = GoogleAIAdapter()
        result = await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")], api_key="fake-key"
        )
        assert result.finish_reason == "length"

    @respx.mock
    @pytest.mark.asyncio
    async def test_safety_finish_reason(self):
        respx.post(f"{GOOGLE_BASE_URL}/models/gemini-1.5-flash:generateContent").mock(
            return_value=httpx.Response(200, json=_gemini_response(finish="SAFETY"))
        )
        adapter = GoogleAIAdapter()
        result = await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")], api_key="fake-key"
        )
        assert result.finish_reason == "content_filter"


class TestGoogleAIRequestShaping:
    def test_system_messages_split_out(self):
        msgs = [
            Message(role="system", content="You are helpful."),
            Message(role="user", content="Hi"),
            Message(role="assistant", content="Hello!"),
        ]
        rest, system_text = _split_system(msgs)
        assert system_text == "You are helpful."
        assert len(rest) == 2
        assert [m.role for m in rest] == ["user", "assistant"]

    def test_multiple_system_messages_concatenated(self):
        msgs = [
            Message(role="system", content="Be concise."),
            Message(role="system", content="Be friendly."),
            Message(role="user", content="Hi"),
        ]
        rest, system_text = _split_system(msgs)
        assert "Be concise." in system_text
        assert "Be friendly." in system_text

    def test_assistant_role_mapped_to_model(self):
        msgs = [
            Message(role="user", content="Q"),
            Message(role="assistant", content="A"),
            Message(role="user", content="Q2"),
        ]
        contents = _build_contents(msgs)
        assert contents[0]["role"] == "user"
        assert contents[1]["role"] == "model"
        assert contents[2]["role"] == "user"
        assert contents[1]["parts"][0]["text"] == "A"

    @respx.mock
    @pytest.mark.asyncio
    async def test_system_instruction_sent_in_body(self):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=_gemini_response())

        respx.post(
            f"{GOOGLE_BASE_URL}/models/gemini-1.5-flash:generateContent"
        ).mock(side_effect=handler)

        adapter = GoogleAIAdapter()
        await adapter.chat(
            _make_payload(),
            [
                Message(role="system", content="Be brief."),
                Message(role="user", content="Hi"),
            ],
            api_key="fake-key",
        )
        body = captured["body"]
        assert "systemInstruction" in body
        assert body["systemInstruction"]["parts"][0]["text"] == "Be brief."
        assert body["contents"][0]["role"] == "user"

    @respx.mock
    @pytest.mark.asyncio
    async def test_generation_config_forwarded(self):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=_gemini_response())

        respx.post(
            f"{GOOGLE_BASE_URL}/models/gemini-1.5-flash:generateContent"
        ).mock(side_effect=handler)

        adapter = GoogleAIAdapter()
        await adapter.chat(
            _make_payload(metadata={"temperature": 0.7, "max_tokens": 256, "top_p": 0.9}),
            [Message(role="user", content="Hi")],
            api_key="fake-key",
        )
        gen = captured["body"]["generationConfig"]
        assert gen["temperature"] == 0.7
        assert gen["maxOutputTokens"] == 256
        assert gen["topP"] == 0.9


class TestGoogleAIStreaming:
    @respx.mock
    @pytest.mark.asyncio
    async def test_streams_text_chunks(self):
        sse_body = (
            'data: {"candidates":[{"content":{"role":"model","parts":[{"text":"Hello"}]}}]}\n'
            'data: {"candidates":[{"content":{"role":"model","parts":[{"text":", world"}]}}]}\n'
            'data: {"candidates":[{"content":{"role":"model","parts":[{"text":"!"}]},"finishReason":"STOP"}],'
            '"usageMetadata":{"promptTokenCount":3,"candidatesTokenCount":4,"totalTokenCount":7}}\n'
        )
        respx.post(
            f"{GOOGLE_BASE_URL}/models/gemini-1.5-flash:streamGenerateContent"
        ).mock(
            return_value=httpx.Response(
                200,
                content=sse_body.encode("utf-8"),
                headers={"content-type": "text/event-stream"},
            )
        )
        adapter = GoogleAIAdapter()
        chunks: list[ProviderStreamChunk] = []
        async for chunk in adapter.stream_chat(
            _make_payload(), [Message(role="user", content="Hi")], api_key="fake-key"
        ):
            chunks.append(chunk)

        assert [c.delta for c in chunks] == ["Hello", ", world", "!"]
        assert chunks[-1].finish_reason == "stop"
        assert chunks[-1].usage is not None
        assert chunks[-1].usage.total_tokens == 7
