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
def _no_inherited_health_markers():
    """Clear the provider health markers this file's routing depends on.

    FIX57 — these tests assert on what happens when the PRIMARY model
    fails. When an earlier test in the lane had marked openai/gpt-4o-mini
    unhealthy, the streaming pre-check skipped it before the adapter was
    ever called and the turn was answered by the anthropic stub instead:
    no error frame, no partial content, and a failure message that read
    like the error propagation was broken.

    The markers live in a shared Redis with a 60-second TTL, so whether
    they were still set when this file ran depended on how long the
    preceding tests had taken — one of the last two things drifting in an
    otherwise fixed-order, fixed-seed lane.
    """
    from app.services._redis_pool import run_sync
    from app.services.gateway.fallback_orchestrator import clear_provider_health

    for provider in ("openai", "anthropic"):
        try:
            run_sync(clear_provider_health(provider, "gpt-4o-mini"))
            run_sync(clear_provider_health(provider))
        except Exception:  # noqa: BLE001 — no Redis here is not a failure
            pass
    yield


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


def test_pre_stream_non_eligible_failure_logs_upstream_detail(monkeypatch):
    """The terminal (non-fallback-eligible) branch must LOG the real
    upstream error — status, provider and response body from exc.details,
    the same detail already shipped in the SSE frame. Until this log line
    existed, the only server-side trace was the finalize hook's bare
    ``stream_partial_fail``, which made e.g. the GPT-5 ``max_tokens`` 400
    invisible in production logs.

    Asserted by patching the route module's ``logger`` (which the endpoint
    ``.bind()``s into its per-request ``log``) rather than
    ``structlog.testing.capture_logs`` — the cached module logger makes
    capture_logs order-dependent under the full suite."""
    from app.core.exceptions import UpstreamBadResponseError

    class _CapturingLogger:
        def __init__(self):
            self.records: list[dict] = []

        def bind(self, **kw):
            return self

        def _rec(self, level, event, **kw):
            self.records.append({"log_level": level, "event": event, **kw})

        def debug(self, event, **kw):
            self._rec("debug", event, **kw)

        def info(self, event, **kw):
            self._rec("info", event, **kw)

        def warning(self, event, **kw):
            self._rec("warning", event, **kw)

        def error(self, event, **kw):
            self._rec("error", event, **kw)

        def exception(self, event, **kw):
            self._rec("error", event, **kw)

    upstream_body = {
        "error": {
            "message": (
                "Unsupported parameter: 'max_tokens' is not supported with "
                "this model. Use 'max_completion_tokens' instead."
            ),
            "type": "invalid_request_error",
            "param": "max_tokens",
            "code": "unsupported_parameter",
        }
    }

    async def _raise_400(
        self, payload, messages, *, resolved_model=None, api_key=None
    ) -> AsyncIterator[ProviderStreamChunk]:
        raise UpstreamBadResponseError(
            "Provider 'openai' rejected request (400).",
            details={"provider": "openai", "status": 400, "body": upstream_body},
        )
        yield  # pragma: no cover

    monkeypatch.setattr(OpenAIAdapter, "stream_chat", _raise_400)
    cap = _CapturingLogger()
    monkeypatch.setattr("app.api.routes.public_chat.logger", cap)

    resp = _chat_stream()
    events = _parse_sse(resp.content)

    # The client-facing frame is unchanged…
    errors = [e for e in events if "error" in e]
    assert errors, f"expected an error frame, got {events}"
    assert errors[0]["error"]["code"] == "upstream_bad_response"

    # …and the REAL error is now in the server log.
    records = [
        rec
        for rec in cap.records
        if rec.get("event") == "stream_terminal_provider_error"
    ]
    assert records, (
        "terminal provider error was not logged; events seen: "
        f"{[rec.get('event') for rec in cap.records]}"
    )
    rec = records[0]
    assert rec["error_code"] == "upstream_bad_response"
    assert rec["details"]["status"] == 400
    assert rec["details"]["provider"] == "openai"
    assert "max_completion_tokens" in str(rec["details"]["body"])
    assert rec["model"]
