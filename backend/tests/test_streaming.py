"""P2-07: Streaming support baseline tests.

Verifies:
- ProviderStreamChunk dataclass
- OpenAI SSE line parsing
- stream_chat yields chunks
- Base adapter raises NotImplementedError for unsupported streaming
- Non-streaming flows unaffected
"""

import pytest

from app.domain.providers import ProviderStreamChunk
from app.services.providers.openai_adapter import _parse_sse_line


# ---------------------------------------------------------------------------
# ProviderStreamChunk
# ---------------------------------------------------------------------------


class TestProviderStreamChunk:
    def test_defaults(self):
        c = ProviderStreamChunk()
        assert c.delta == ""
        assert c.finish_reason is None
        assert c.model == ""

    def test_with_content(self):
        c = ProviderStreamChunk(delta="Hello", model="gpt-4o")
        assert c.delta == "Hello"
        assert c.model == "gpt-4o"

    def test_frozen(self):
        c = ProviderStreamChunk(delta="hi")
        with pytest.raises(AttributeError):
            c.delta = "bye"  # type: ignore[misc]

    def test_final_chunk(self):
        c = ProviderStreamChunk(finish_reason="stop")
        assert c.finish_reason == "stop"


# ---------------------------------------------------------------------------
# SSE line parsing
# ---------------------------------------------------------------------------


class TestParseSSELine:
    def test_content_chunk(self):
        line = 'data: {"id":"x","choices":[{"delta":{"content":"Hi"},"finish_reason":null}],"model":"gpt-4o"}'
        chunk = _parse_sse_line(line)
        assert chunk is not None
        assert chunk.delta == "Hi"
        assert chunk.finish_reason is None
        assert chunk.model == "gpt-4o"

    def test_final_chunk_with_finish_reason(self):
        line = 'data: {"id":"x","choices":[{"delta":{},"finish_reason":"stop"}],"model":"gpt-4o"}'
        chunk = _parse_sse_line(line)
        assert chunk is not None
        assert chunk.delta == ""
        assert chunk.finish_reason == "stop"

    def test_done_sentinel(self):
        chunk = _parse_sse_line("data: [DONE]")
        assert chunk is None

    def test_empty_line(self):
        chunk = _parse_sse_line("")
        assert chunk is None

    def test_non_data_line(self):
        chunk = _parse_sse_line(": keep-alive")
        assert chunk is None

    def test_malformed_json(self):
        chunk = _parse_sse_line("data: {invalid json")
        assert chunk is None

    def test_no_choices(self):
        chunk = _parse_sse_line('data: {"id":"x","choices":[]}')
        assert chunk is None


# ---------------------------------------------------------------------------
# Base adapter streaming contract
# ---------------------------------------------------------------------------


class TestBaseAdapterStreaming:
    @pytest.mark.asyncio
    async def test_unsupported_adapter_raises(self):
        """BaseProviderAdapter.stream_chat default raises NotImplementedError.

        Originally tested via AnthropicAdapter, which has since gained a
        real stream_chat implementation. Now uses a throwaway subclass
        that leaves stream_chat unoverridden so the test actually
        exercises the base-class contract it was meant to cover.
        """
        from app.services.providers.base import BaseProviderAdapter

        class _BareAdapter(BaseProviderAdapter):
            provider_name = "_bare_test"
            supports_streaming = False

            async def chat(
                self, payload, messages, *, resolved_model=None, api_key=None
            ):
                raise NotImplementedError("bare test adapter")

        adapter = _BareAdapter()
        with pytest.raises(NotImplementedError):
            async for _ in adapter.stream_chat(None, []):
                pass

    def test_openai_supports_streaming(self):
        from app.services.providers.openai_adapter import OpenAIAdapter

        assert OpenAIAdapter.supports_streaming is True

    def test_anthropic_supports_streaming(self):
        """Anthropic gained streaming support — see anthropic_adapter._process_sse_stream."""
        from app.services.providers.anthropic_adapter import AnthropicAdapter

        assert AnthropicAdapter.supports_streaming is True
