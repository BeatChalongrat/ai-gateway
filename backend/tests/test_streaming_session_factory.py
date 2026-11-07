"""Streaming-path session-factory contract tests (known-issues #6).

Locks the contract that ``event_generator`` mints its post-stream
log session via the factory captured by the route handler at
*request time* — NOT via a module-global lookup at generator-run
time and NOT via a binding captured at module-import time.

Two failure modes these tests guard against:

  * **Module-level capture.** A future refactor that does
    ``log_session_factory = SessionLocal`` at the top of
    ``public_chat.py`` would freeze the factory at import time,
    silently ignoring any subsequent monkeypatch or future
    request-time tenant-aware factory selection.
  * **Capture at import time.** Any indirection that resolves the
    factory once (e.g. via ``app.state`` set at startup) and then
    holds it forever — same problem, harder to spot in review.

The benign-stream-persists-log-row regression case is already
covered by ``test_streaming_routing.py``'s four router tests
(``_log_for(request_id)`` reads ``request_logs`` from the
integration engine, so a broken persist mechanism would fail those
tests too).  We don't repeat it here.
"""

from __future__ import annotations

from typing import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.domain.providers import ProviderStreamChunk
from app.main import app
from app.services.providers.openai_adapter import OpenAIAdapter
from tests.conftest import _integration_engine


client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"Authorization": "Bearer demo-key"}


def _chat_stream(content: str):
    return client.post(
        "/v1/chat/completions",
        headers=_HEADERS,
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": content}],
            "metadata": {},
            "stream": True,
        },
    )


def _install_stream_spy(monkeypatch, chunks: list[str] | None = None) -> None:
    """Stub ``OpenAIAdapter.stream_chat`` so no real provider is hit."""
    chunks = chunks or ["ok"]

    async def _spy(
        self,
        payload,
        messages,
        *,
        resolved_model: str | None = None,
        api_key: str | None = None,
    ) -> AsyncIterator[ProviderStreamChunk]:
        for i, text in enumerate(chunks):
            is_last = i == len(chunks) - 1
            yield ProviderStreamChunk(
                delta=text,
                finish_reason="stop" if is_last else None,
                model=resolved_model or "gpt-4o-mini",
            )

    monkeypatch.setattr(OpenAIAdapter, "stream_chat", _spy)


class _CountingFactory:
    """Sessionmaker wrapper that counts the POST-STREAM calls.

    Each instance is independently identifiable, so a test can swap
    one factory for another between requests and assert which one
    the streaming path actually called.  The ``label`` is for
    error messages.

    FIX126 O8 — ``was_called`` counts only the calls made from
    ``_finalize_stream`` (the post-stream log persist + credit
    deduction, the sites that use the factory captured at route entry).
    Other request-time work (FIX120/125 policy and rescue reads) now
    opens its own short sync sessions through the same global
    ``SessionLocal``; counting those made "exactly one call" false
    without the capture contract changing. ``total`` keeps the raw count.
    """

    def __init__(self, label: str):
        self.label = label
        self.was_called = 0
        self.total = 0
        self._inner = sessionmaker(
            bind=_integration_engine, autoflush=False, autocommit=False
        )

    def __call__(self):
        import sys

        self.total += 1
        if sys._getframe(1).f_code.co_name == "_finalize_stream":
            self.was_called += 1
        return self._inner()


class TestStreamingUsesRequestTimeFactory:
    """The factory captured at route entry is the one that gets called."""

    def test_streaming_uses_request_time_session_factory(self, monkeypatch):
        # The autouse fixture in conftest already patched
        # ``app.core.db.SessionLocal``.  Override it with a counting
        # spy so we can prove it's the one event_generator reaches.
        factory = _CountingFactory("request_time")
        monkeypatch.setattr("app.core.db.SessionLocal", factory)
        _install_stream_spy(monkeypatch)

        resp = _chat_stream("hello")

        assert resp.status_code == 200
        assert factory.was_called >= 1, (
            f"Expected the post-stream persist to call the captured factory; got "
            f"{factory.was_called}.  The factory must be the one bound "
            f"at route entry, not a stale module-level reference."
        )


class TestStreamingFactoryCaptureIsPerRequest:
    """Each request captures its OWN factory at entry — proves the
    factory choice is a per-request decision, not module-level state."""

    def test_streaming_factory_capture_is_per_request(self, monkeypatch):
        factory_a = _CountingFactory("FactoryA")
        factory_b = _CountingFactory("FactoryB")
        _install_stream_spy(monkeypatch)

        # Request 1 — factory A is current at entry.
        monkeypatch.setattr("app.core.db.SessionLocal", factory_a)
        resp1 = _chat_stream("first request")
        assert resp1.status_code == 200
        a_after_1 = factory_a.was_called
        assert a_after_1 >= 1, (
            f"Request 1: FactoryA expected 1 call, got "
            f"{factory_a.was_called}"
        )
        assert factory_b.was_called == 0, (
            f"Request 1 should NOT have touched FactoryB; got "
            f"{factory_b.was_called}"
        )

        # Swap to factory B before request 2.
        monkeypatch.setattr("app.core.db.SessionLocal", factory_b)
        resp2 = _chat_stream("second request")
        assert resp2.status_code == 200
        # FactoryA must NOT have been called again — that would prove
        # the capture point is module-level state held across requests.
        assert factory_a.was_called == a_after_1, (
            f"FactoryA call count drifted from {a_after_1} to "
            f"{factory_a.was_called}.  This means request 2 reused a "
            f"factory captured at module import or in cross-request "
            f"state — the contract is per-request capture."
        )
        # FactoryB must have been called for request 2's post-stream work.
        assert factory_b.was_called >= 1, (
            f"Request 2: FactoryB expected 1 call, got "
            f"{factory_b.was_called}.  If 0, the route handler is "
            f"holding a stale factory captured before request 2 "
            f"started."
        )
