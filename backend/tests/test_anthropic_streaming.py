"""Tests for Anthropic streaming support.

Two layers:

1. **Parser unit tests** — drive ``AnthropicAdapter._process_sse_stream``
   with a hand-rolled async iterator of fake SSE lines and verify it
   emits the expected ``ProviderStreamChunk`` sequence. No httpx or
   real network needed; the parser is the hard part and deserves
   targeted coverage.

2. **Integration smoke tests** — verify the class-level
   ``supports_streaming`` attribute + model registry flip, and drive
   the FastAPI ``/v1/chat/completions`` endpoint with a monkeypatched
   ``stream_chat`` to prove the routing → adapter path lines up for
   Claude models.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from app.domain.providers import ProviderStreamChunk, ProviderUsage
from app.main import app
from app.services.providers.anthropic_adapter import AnthropicAdapter
from app.services.providers.model_registry import get_candidate


client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"Authorization": "Bearer demo-key"}


# ---------------------------------------------------------------------------
# Parser unit tests
# ---------------------------------------------------------------------------


async def _aiter(items: list[str]) -> AsyncIterator[str]:
    """Async generator helper — yields strings one at a time."""
    for item in items:
        yield item


def _sse(event: str, payload: dict) -> list[str]:
    """Build the SSE line pair for a single Anthropic event.

    Mimics what httpx.aiter_lines() would produce for one event —
    two content lines (event, data) plus a blank separator.
    """
    return [f"event: {event}", f"data: {json.dumps(payload)}", ""]


async def _collect(agen) -> list[ProviderStreamChunk]:
    return [chunk async for chunk in agen]


@pytest.mark.asyncio
async def test_parser_yields_text_deltas_in_order():
    adapter = AnthropicAdapter()
    lines = (
        _sse("message_start", {
            "type": "message_start",
            "message": {
                "id": "msg_1",
                "model": "claude-3-5-sonnet-20241022",
                "usage": {"input_tokens": 25, "output_tokens": 1},
            },
        })
        + _sse("content_block_start", {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        })
        + _sse("content_block_delta", {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Hello"},
        })
        + _sse("content_block_delta", {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": ", world"},
        })
        + _sse("content_block_delta", {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "!"},
        })
        + _sse("content_block_stop", {"type": "content_block_stop", "index": 0})
        + _sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 11},
        })
        + _sse("message_stop", {"type": "message_stop"})
    )

    chunks = await _collect(
        adapter._process_sse_stream(_aiter(lines), initial_model="claude-init")
    )

    # 3 text deltas + 1 final chunk.
    assert len(chunks) == 4
    text_deltas = [c.delta for c in chunks if c.delta]
    assert text_deltas == ["Hello", ", world", "!"]
    # Model captured from message_start.
    assert all(c.model == "claude-3-5-sonnet-20241022" for c in chunks)


@pytest.mark.asyncio
async def test_parser_final_chunk_carries_usage_and_finish_reason():
    adapter = AnthropicAdapter()
    lines = (
        _sse("message_start", {
            "type": "message_start",
            "message": {
                "id": "msg_2",
                "model": "claude-3-haiku-20240307",
                "usage": {"input_tokens": 12, "output_tokens": 1},
            },
        })
        + _sse("content_block_delta", {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "ok"},
        })
        + _sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": "max_tokens"},
            "usage": {"output_tokens": 8},
        })
        + _sse("message_stop", {"type": "message_stop"})
    )

    chunks = await _collect(
        adapter._process_sse_stream(_aiter(lines), initial_model="init")
    )

    # Final chunk is the last one.
    final = chunks[-1]
    # stop_reason translated from "max_tokens" → "length".
    assert final.finish_reason == "length"
    assert final.usage is not None
    assert final.usage.prompt_tokens == 12
    # output_tokens from message_delta (8) overrides the initial 1.
    assert final.usage.completion_tokens == 8
    assert final.usage.total_tokens == 20


@pytest.mark.asyncio
async def test_parser_ignores_ping_and_unknown_events():
    adapter = AnthropicAdapter()
    lines = (
        _sse("ping", {"type": "ping"})
        + _sse("message_start", {
            "type": "message_start",
            "message": {
                "id": "msg_3",
                "model": "claude-3-haiku-20240307",
                "usage": {"input_tokens": 5, "output_tokens": 1},
            },
        })
        + _sse("ping", {"type": "ping"})
        + _sse("some_future_event", {"type": "some_future_event"})
        + _sse("content_block_delta", {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "hi"},
        })
        + _sse("ping", {"type": "ping"})
        + _sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 2},
        })
        + _sse("message_stop", {"type": "message_stop"})
    )

    chunks = await _collect(
        adapter._process_sse_stream(_aiter(lines), initial_model="init")
    )

    # Pings + unknown events produce no chunks; we should see 1 text
    # delta + 1 final chunk = 2 total.
    assert len(chunks) == 2
    assert chunks[0].delta == "hi"
    assert chunks[1].usage is not None


@pytest.mark.asyncio
async def test_parser_skips_empty_text_deltas():
    """Anthropic occasionally emits empty text_delta events — ignore them
    so downstream never sees zero-length chunks."""
    adapter = AnthropicAdapter()
    lines = (
        _sse("message_start", {
            "type": "message_start",
            "message": {
                "id": "msg_4",
                "model": "claude-3-haiku-20240307",
                "usage": {"input_tokens": 3, "output_tokens": 1},
            },
        })
        + _sse("content_block_delta", {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": ""},
        })
        + _sse("content_block_delta", {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "real"},
        })
        + _sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 1},
        })
        + _sse("message_stop", {"type": "message_stop"})
    )

    chunks = await _collect(
        adapter._process_sse_stream(_aiter(lines), initial_model="init")
    )

    text_chunks = [c for c in chunks if c.delta]
    assert len(text_chunks) == 1
    assert text_chunks[0].delta == "real"


@pytest.mark.asyncio
async def test_parser_ignores_non_text_deltas():
    """tool_use deltas and unknown delta shapes must not produce chunks.

    v1 only supports text streaming; tool-use blocks carry a different
    delta shape (``input_json_delta``) that the parser should silently
    pass over rather than emit as text.
    """
    adapter = AnthropicAdapter()
    lines = (
        _sse("message_start", {
            "type": "message_start",
            "message": {
                "id": "msg_5",
                "model": "claude-3-haiku-20240307",
                "usage": {"input_tokens": 3, "output_tokens": 1},
            },
        })
        + _sse("content_block_delta", {
            "type": "content_block_delta",
            "index": 0,
            "delta": {
                "type": "input_json_delta",
                "partial_json": '{"arg":',
            },
        })
        + _sse("content_block_delta", {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "only this"},
        })
        + _sse("message_stop", {"type": "message_stop"})
    )

    chunks = await _collect(
        adapter._process_sse_stream(_aiter(lines), initial_model="init")
    )

    text_chunks = [c for c in chunks if c.delta]
    assert len(text_chunks) == 1
    assert text_chunks[0].delta == "only this"


# ---------------------------------------------------------------------------
# Class-level + registry invariants
# ---------------------------------------------------------------------------


class TestAdapterDeclaresStreamingSupport:
    def test_supports_streaming_class_attr(self):
        assert AnthropicAdapter.supports_streaming is True

    def test_instance_attribute_matches_class(self):
        # Guard against a subclass accidentally overriding to False.
        assert AnthropicAdapter().supports_streaming is True


class TestModelRegistryFlipped:
    @pytest.mark.parametrize(
        "model",
        [
            "claude-3-5-sonnet-20241022",
            "claude-3-opus-20240229",
            "claude-3-haiku-20240307",
        ],
    )
    def test_all_claude_models_marked_streaming(self, model):
        c = get_candidate(model)
        assert c is not None
        assert c.supports_streaming is True


# ---------------------------------------------------------------------------
# End-to-end through /v1/chat/completions with a monkeypatched adapter
# ---------------------------------------------------------------------------


class TestStreamingE2EViaPublicChat:
    def test_claude_streaming_request_returns_sse(self, monkeypatch):
        """Routing a stream=true request for a Claude model must land
        in AnthropicAdapter.stream_chat and come back as SSE.

        This test patches stream_chat to yield fake chunks so no real
        Anthropic API call is made. It's the smallest integration test
        that proves routing + adapter selection + SSE serialization all
        align for Claude models.
        """
        captured: dict = {"called": False, "resolved_model": None}

        async def _spy(
            self,
            payload,
            messages,
            *,
            resolved_model: str | None = None,
            api_key: str | None = None,
        ):
            captured["called"] = True
            captured["resolved_model"] = resolved_model
            yield ProviderStreamChunk(
                delta="hello ", model=resolved_model or "claude"
            )
            yield ProviderStreamChunk(
                delta="from claude",
                finish_reason="stop",
                model=resolved_model or "claude",
                usage=ProviderUsage(
                    prompt_tokens=10,
                    completion_tokens=4,
                    total_tokens=14,
                ),
            )

        monkeypatch.setattr(AnthropicAdapter, "stream_chat", _spy)

        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={
                "model": "claude-3-haiku-20240307",
                "messages": [{"role": "user", "content": "hi"}],
                "metadata": {},
                "stream": True,
            },
        )

        assert resp.status_code == 200
        assert captured["called"] is True
        assert captured["resolved_model"] == "claude-3-haiku-20240307"

        # Parse the SSE body — the streaming branch emits one combined
        # chunk with the full buffered body, followed by [DONE].
        body = resp.content.decode()
        assert "data: [DONE]" in body
        # The concatenated text "hello from claude" must appear in the
        # output chunk (DLP restore pass-through for clean content).
        assert "hello from claude" in body
