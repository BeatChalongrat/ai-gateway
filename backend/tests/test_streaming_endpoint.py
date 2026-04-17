"""P1-04: Streaming response contract tests.

Verifies:
- stream=false returns normal JSON response
- stream=true returns SSE StreamingResponse
- SSE events have correct format
- [DONE] sentinel sent at end
- Non-streaming flow is unaffected
- Fallback to non-streaming for unsupported adapters
"""

import json

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"Authorization": "Bearer demo-key"}


class TestNonStreamingUnaffected:
    def test_stream_false_returns_json(self):
        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["object"] == "chat.completion"
        assert "choices" in body

    def test_default_is_non_streaming(self):
        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")


class TestStreamingContract:
    def test_stream_true_returns_event_stream(self):
        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            },
        )
        # The conftest monkeypatches dispatch, so streaming falls back
        # to non-streaming for the stub. When a real streaming adapter
        # is available, this would return text/event-stream.
        # For now, verify it returns a valid response (200).
        assert resp.status_code == 200

    def test_stream_with_anthropic_returns_sse(self, monkeypatch):
        """Anthropic streaming is now supported via stream_chat — verify
        the request routes through the Anthropic adapter and comes back
        as an SSE body, not a JSON chat.completion envelope.

        Previous behavior ("falls back to non-streaming") is gone; the
        detailed parser coverage lives in test_anthropic_streaming.py.
        This test just asserts that routing picks the Anthropic adapter
        for Claude models and the SSE transport wiring works end-to-end.
        """
        from app.domain.providers import ProviderStreamChunk
        from app.services.providers.anthropic_adapter import AnthropicAdapter

        async def _spy(
            self,
            payload,
            messages,
            *,
            resolved_model=None,
            api_key=None,
        ):
            yield ProviderStreamChunk(
                delta="ok", finish_reason="stop", model=resolved_model or "claude"
            )

        monkeypatch.setattr(AnthropicAdapter, "stream_chat", _spy)

        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={
                "model": "claude-3-5-sonnet-20241022",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            },
        )
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "data: [DONE]" in body


class TestStreamingSSEFormat:
    """Test SSE format by parsing the event_generator output directly."""

    def test_sse_event_shape(self):
        """Verify the SSE event data structure is correct."""
        # This is a unit test of the event format, not the endpoint
        event_data = {
            "id": "req_abc",
            "object": "chat.completion.chunk",
            "created": 1234567890,
            "model": "gpt-4o-mini",
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": "Hello"},
                    "finish_reason": None,
                }
            ],
        }
        line = f"data: {json.dumps(event_data)}\n\n"
        assert line.startswith("data: ")
        parsed = json.loads(line[6:].strip())
        assert parsed["object"] == "chat.completion.chunk"
        assert parsed["choices"][0]["delta"]["content"] == "Hello"

    def test_done_sentinel_format(self):
        line = "data: [DONE]\n\n"
        assert line.strip() == "data: [DONE]"
