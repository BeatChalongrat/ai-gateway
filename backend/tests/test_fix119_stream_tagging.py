"""FIX119 — the provider request id is stamped on the FIRST streamed chunk
through the real stream_chat path (a fake transport, not a parsed line).
Marked ``real_adapter``: the conftest otherwise replaces every adapter's
stream_chat with a stub stream.

The first live run of FIX119 failed every turn with
``dataclasses.FrozenInstanceError``: the chunk dataclass is frozen and the
tagging assigned an attribute. The line-level parser tests could not see
that, because they never ran the wrapper that does the tagging. These do.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.domain.providers import ProviderStreamChunk
from app.services.providers.anthropic_adapter import AnthropicAdapter
from app.services.providers.openai_compatible_adapter import OpenAICompatibleAdapter
from app.schemas.public import ChatCompletionsRequest, Message

OPENAI_BODY = (
    'data: {"id":"chatcmpl-1","model":"m","choices":[{"index":0,"delta":{"content":"cut "},"finish_reason":null}]}\n\n'
    'data: {"id":"chatcmpl-1","model":"m","choices":[{"index":0,"delta":{"content":"off"},"finish_reason":null}]}\n\n'
    'data: {"id":"chatcmpl-1","model":"m","choices":[{"index":0,"delta":{},"finish_reason":"length"}]}\n\n'
    "data: [DONE]\n\n"
)
ANTHROPIC_BODY = (
    "event: message_start\n"
    'data: {"type":"message_start","message":{"model":"claude-x","usage":{"input_tokens":3}}}\n\n'
    "event: content_block_delta\n"
    'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"half"}}\n\n'
    "event: message_delta\n"
    'data: {"type":"message_delta","delta":{"stop_reason":"max_tokens"},"usage":{"output_tokens":24}}\n\n'
    "event: message_stop\n"
    'data: {"type":"message_stop"}\n\n'
)


def _req(text: str = "hi") -> ChatCompletionsRequest:
    return ChatCompletionsRequest(model="m", messages=[Message(role="user", content=text)], stream=True)


class _Compat(OpenAICompatibleAdapter):
    provider_name = "sranai"

    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def _get_client(self, api_key=None):  # noqa: D401 — test seam
        return self._client

    def _request_headers(self, api_key=None):
        return {}


@pytest.mark.asyncio
@pytest.mark.real_adapter
async def test_openai_compatible_stream_tags_first_chunk_with_provider_request_id():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, headers={"x-request-id": "prov-42", "content-type": "text/event-stream"}, content=OPENAI_BODY.encode())
    )
    adapter = _Compat(httpx.AsyncClient(transport=transport, base_url="http://box.test/v1"))
    chunks = [c async for c in adapter.stream_chat(_req(), [Message(role="user", content="hi")], resolved_model="m")]
    assert all(isinstance(c, ProviderStreamChunk) for c in chunks)
    assert chunks[0].provider_request_id == "prov-42"
    assert chunks[1].provider_request_id is None
    assert chunks[-1].finish_reason == "length"
    assert chunks[-1].stop_reason == "max_tokens"
    assert "".join(c.delta for c in chunks) == "cut off"


@pytest.mark.asyncio
@pytest.mark.real_adapter
@respx.mock
async def test_anthropic_stream_tags_first_chunk_and_reads_stop_reason_from_message_delta():
    # respx intercepts whatever client the adapter's pool hands out.
    respx.post(url__regex=r".*/messages$").mock(
        return_value=httpx.Response(200, headers={"request-id": "req_anthropic_7", "content-type": "text/event-stream"}, content=ANTHROPIC_BODY.encode())
    )
    adapter = AnthropicAdapter()
    chunks = [c async for c in adapter.stream_chat(_req(), [Message(role="user", content="hi")], resolved_model="claude-x", api_key="k")]
    assert chunks, "the adapter yielded nothing"
    assert chunks[0].provider_request_id == "req_anthropic_7"
    assert chunks[-1].finish_reason == "length"
    assert chunks[-1].stop_reason == "max_tokens"
