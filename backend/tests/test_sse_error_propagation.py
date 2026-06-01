"""Group B / B1 — gateway SSE error propagation.

When a streaming request fails, the gateway must emit an SSE ``error`` frame
instead of silently truncating the stream (the 200 + headers are already on
the wire by the time the generator runs, so raising just produces a blank
screen). Covers three terminal cases:

* pre-stream failure with no fallback candidate → ``all_models_failed``
  frame carrying the per-model ``attempts`` log (B3),
* mid-stream failure after content started → ``provider_error`` frame
  appended after the partial content,
* pre-stream non-eligible failure → ``provider_error`` frame.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import ProviderError
from app.domain.providers import ProviderStreamChunk
from app.main import app
from app.services.gateway.fallback_orchestrator import FallbackOrchestrator
from app.services.providers.openai_adapter import OpenAIAdapter
from tests.conftest import _integration_engine

client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"Authorization": "Bearer demo-key"}


@pytest.fixture(autouse=True)
def _route_session_local(monkeypatch):
    integration_sessionmaker = sessionmaker(
        bind=_integration_engine, autoflush=False, autocommit=False
    )
    monkeypatch.setattr("app.core.db.SessionLocal", integration_sessionmaker)


@pytest.fixture(autouse=True)
def _no_streaming_fallback_candidate(monkeypatch):
    """Force the in-generator fallback selector to find nothing, so the
    failure is terminal and the generator must emit an error frame."""
    monkeypatch.setattr(
        FallbackOrchestrator,
        "select_streaming_candidate",
        lambda self, **kw: None,
    )


def _parse_sse(body: bytes | str) -> list[dict]:
    if isinstance(body, bytes):
        body = body.decode()
    events: list[dict] = []
    for line in body.splitlines():
        if not line.startswith("data: "):
            continue
        data = line[6:].strip()
        if data == "[DONE]":
            continue
        events.append(json.loads(data))
    return events


def _chat_stream(content: str = "hello", model: str = "gpt-4o-mini"):
    return client.post(
        "/v1/chat/completions",
        headers=_HEADERS,
        json={
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "metadata": {},
            "stream": True,
        },
    )


def test_pre_stream_failure_emits_all_models_failed(monkeypatch):
    async def _raise_immediately(
        self, payload, messages, *, resolved_model=None, api_key=None
    ) -> AsyncIterator[ProviderStreamChunk]:
        raise ProviderError(
            "rejected credentials (401)",
            details={"provider": "openai", "status": 401},
        )
        yield  # pragma: no cover — makes this an async generator

    monkeypatch.setattr(OpenAIAdapter, "stream_chat", _raise_immediately)

    resp = _chat_stream()
    events = _parse_sse(resp.content)
    errors = [e for e in events if "error" in e]

    assert errors, f"expected an error frame, got {events}"
    err = errors[0]["error"]
    assert err["code"] == "all_models_failed"
    assert isinstance(err.get("attempts"), list) and err["attempts"]


def test_mid_stream_failure_emits_error_after_content(monkeypatch):
    # Emit > MIN_TAIL_CHARS (120) of content so the rolling-window DLP
    # buffer actually flushes a content frame (and ``first_chunk_yielded``
    # flips True) BEFORE the failure — that's what makes this the
    # mid-stream path rather than the pre-stream path.
    long_text = "This is a long partial answer that exceeds the tail. " * 4

    async def _yield_then_raise(
        self, payload, messages, *, resolved_model=None, api_key=None
    ) -> AsyncIterator[ProviderStreamChunk]:
        yield ProviderStreamChunk(
            delta=long_text, finish_reason=None, model="gpt-4o-mini"
        )
        raise ProviderError(
            "connection reset",
            details={"provider": "openai", "status": 502},
        )

    monkeypatch.setattr(OpenAIAdapter, "stream_chat", _yield_then_raise)

    resp = _chat_stream()
    events = _parse_sse(resp.content)
    errors = [e for e in events if "error" in e]
    content = [
        e
        for e in events
        if e.get("choices", [{}])[0].get("delta", {}).get("content")
    ]

    assert content, "expected the partial content frame to survive"
    assert errors, f"expected a mid-stream error frame, got {events}"
    assert errors[0]["error"]["code"] in ("provider_error", "provider_unavailable")


def test_pre_stream_non_eligible_failure_emits_error(monkeypatch):
    async def _raise_value_error(
        self, payload, messages, *, resolved_model=None, api_key=None
    ) -> AsyncIterator[ProviderStreamChunk]:
        raise ValueError("not a gateway error")
        yield  # pragma: no cover

    monkeypatch.setattr(OpenAIAdapter, "stream_chat", _raise_value_error)

    resp = _chat_stream()
    events = _parse_sse(resp.content)
    errors = [e for e in events if "error" in e]

    assert errors, f"expected an error frame, got {events}"
    assert errors[0]["error"]["code"] == "provider_error"


def test_successful_fallback_emits_fallback_info_metadata(monkeypatch):
    """B2 — when an in-generator fallback rescues the request, a
    ``fallback_info`` frame is emitted before the answering content."""
    from app.services.providers.anthropic_adapter import AnthropicAdapter

    async def _primary_fails(
        self, payload, messages, *, resolved_model=None, api_key=None
    ) -> AsyncIterator[ProviderStreamChunk]:
        raise ProviderError(
            "rejected credentials (401)",
            details={"provider": "openai", "status": 401},
        )
        yield  # pragma: no cover

    async def _fallback_answers(
        self, payload, messages, *, resolved_model=None, api_key=None
    ) -> AsyncIterator[ProviderStreamChunk]:
        yield ProviderStreamChunk(
            delta="answer from the fallback model. " * 6,
            finish_reason="stop",
            model=resolved_model or "claude-sonnet-4",
        )

    monkeypatch.setattr(OpenAIAdapter, "stream_chat", _primary_fails)
    monkeypatch.setattr(AnthropicAdapter, "stream_chat", _fallback_answers)
    # Override the autouse "no candidate" stub: hand back an Anthropic
    # candidate so the in-generator fallback succeeds.
    monkeypatch.setattr(
        FallbackOrchestrator,
        "select_streaming_candidate",
        lambda self, **kw: {
            "provider": "anthropic",
            "model": "claude-sonnet-4",
            "api_key": "test-stub-key",
        },
    )

    resp = _chat_stream()
    events = _parse_sse(resp.content)
    fb = [e for e in events if e.get("type") == "fallback_info"]

    assert fb, f"expected a fallback_info frame, got {events}"
    info = fb[0]["fallback_info"]
    assert info["actual_model"] == "claude-sonnet-4"
    assert info["requested_model"] == "gpt-4o-mini"
