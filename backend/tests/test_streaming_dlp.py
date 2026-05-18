"""End-to-end DLP tests for the streaming chat completions path.

Mirrors the mask → provider → restore → log flow verified for the
non-streaming path in ``test_e2e_dlp_flow.py``, but exercises the SSE
endpoint in ``public_chat.py``.

Each test monkeypatches ``OpenAIAdapter.stream_chat`` with an async
generator that captures the messages it receives and yields a
configurable sequence of ``ProviderStreamChunk`` instances. The test
then drives the FastAPI ``TestClient`` against
``POST /v1/chat/completions`` with ``stream=true`` and parses the
resulting SSE body.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.domain.providers import ProviderStreamChunk
from app.main import app
from app.models.request_log import RequestLogORM
from app.services.policy.profiles import STRICT_PROFILE
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


def _install_stream_spy(
    monkeypatch, chunks: list[str], finish_reason: str = "stop"
):
    """Replace OpenAIAdapter.stream_chat with a capturing async generator.

    Records the ``messages`` argument it received into ``captured`` and
    yields each string in ``chunks`` as a ProviderStreamChunk delta.
    The last chunk carries ``finish_reason``.
    """
    captured: dict = {"messages": None, "called": False}

    async def _spy(
        self,
        payload,
        messages,
        *,
        resolved_model: str | None = None,
        api_key: str | None = None,
    ) -> AsyncIterator[ProviderStreamChunk]:
        captured["messages"] = [m.content for m in messages]
        captured["called"] = True
        for i, text in enumerate(chunks):
            is_last = i == len(chunks) - 1
            yield ProviderStreamChunk(
                delta=text,
                finish_reason=finish_reason if is_last else None,
                model=resolved_model or "gpt-4o-mini",
            )

    monkeypatch.setattr(OpenAIAdapter, "stream_chat", _spy)
    return captured


def _parse_sse(body: bytes | str) -> list[dict]:
    """Parse an SSE body into a list of JSON payload dicts.

    Ignores the trailing ``data: [DONE]`` sentinel.
    """
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


def _full_content(events: list[dict]) -> str:
    """Concatenate the delta.content across all chunk events."""
    out: list[str] = []
    for ev in events:
        delta = ev.get("choices", [{}])[0].get("delta", {})
        if "content" in delta and delta["content"]:
            out.append(delta["content"])
    return "".join(out)


def _log_for(request_id: str) -> RequestLogORM | None:
    Session = sessionmaker(bind=_integration_engine)
    s = Session()
    try:
        return s.get(RequestLogORM, request_id)
    finally:
        s.close()


class TestStreamingPiiMaskedToProvider:
    def test_streaming_pii_masked_to_provider(self, monkeypatch):
        # Adapter echoes the masked input so the captured.messages
        # assertion works even if restore fires later.
        captured = _install_stream_spy(
            monkeypatch,
            chunks=["ack: check your inbox"],
        )

        resp = _chat_stream("my email is john@acme.com, summarise this")

        assert resp.status_code == 200
        user_msgs = [m for m in captured["messages"] if "john" in m or "[EMAIL_0]" in m]
        assert len(user_msgs) >= 1
        sent = user_msgs[0]
        assert "john@acme.com" not in sent
        assert "[EMAIL_0]" in sent


class TestStreamingPiiRestoredInResponse:
    def test_streaming_pii_restored_in_response(self, monkeypatch):
        # Adapter returns a reply containing the indexed token; the
        # public_chat restore step should rewrite it back to the
        # original value.
        _install_stream_spy(
            monkeypatch,
            chunks=["reply to ", "[EMAIL_0]", " tomorrow"],
        )

        resp = _chat_stream("my email is john@acme.com, draft a reply")

        assert resp.status_code == 200
        events = _parse_sse(resp.content)
        full = _full_content(events)
        assert "john@acme.com" in full
        assert "[EMAIL_0]" not in full


class TestStreamingBlockReturns403:
    def test_streaming_block_returns_403(self, monkeypatch):
        # Force strict profile so credential → BLOCK.
        monkeypatch.setattr(
            "app.api.routes.public_chat.get_runtime_profile",
            lambda _session, _pid: STRICT_PROFILE,
        )
        captured = _install_stream_spy(monkeypatch, chunks=["should never run"])

        key = "sk-ant-api03-" + ("a" * 40)
        resp = _chat_stream(f"my key is {key}")

        assert resp.status_code == 403
        body = resp.json()
        assert body["error"]["code"] == "dlp_violation"
        assert captured["called"] is False

        # Streaming DLP block must persist a row — same compliance
        # contract as the non-streaming path (test_e2e_dlp_flow.py).
        # The streaming variant runs the block check BEFORE mask, so
        # input_masked is intentionally None on this row (no masking
        # happens on a blocked input).
        from sqlalchemy.orm import sessionmaker
        Session = sessionmaker(bind=_integration_engine)
        s = Session()
        try:
            row = (
                s.query(RequestLogORM)
                .filter(RequestLogORM.error_code == "dlp_violation")
                .order_by(RequestLogORM.created_at.desc())
                .first()
            )
        finally:
            s.close()
        assert row is not None, "expected a BLOCKED row for the streaming DLP-blocked request"
        assert row.status == "blocked"
        assert row.policy_action == "block"
        assert row.error_code == "dlp_violation"
        assert row.input_raw == "[BLOCKED_BY_DLP]"
        assert key not in (row.input_raw or "")
        assert key not in (row.input_masked or "")
        assert key not in (row.dlp_findings_json or "")


class TestStreamingProviderPiiMaskedUnderDefault:
    def test_streaming_provider_pii_is_masked_by_default(self, monkeypatch):
        """Streaming counterpart of the non-streaming outbound-mask test.

        Outbound PII under DEFAULT_PROFILE now MASKs (May 2026
        tightening). The streaming path shares the same direction-aware
        evaluator as non-streaming, so an AI-introduced email gets
        replaced with an indexed token before the user sees the SSE
        body. Closes the same leak surface RT-04 caught in the audit.
        """
        captured = _install_stream_spy(
            monkeypatch,
            chunks=["contact jane@example.com for help"],
        )

        resp = _chat_stream("who should I contact for help?")

        assert resp.status_code == 200
        # Input had no PII → adapter received original text unchanged.
        [sent] = captured["messages"]
        assert sent == "who should I contact for help?"

        events = _parse_sse(resp.content)
        full = _full_content(events)
        # Provider-introduced PII is now masked before the user sees it.
        assert "jane@example.com" not in full
        assert "[EMAIL_" in full


class TestStreamingCleanMessagePassesThrough:
    def test_streaming_clean_message_passes_through(self, monkeypatch):
        captured = _install_stream_spy(monkeypatch, chunks=["4"])

        resp = _chat_stream("what is 2 + 2?")

        assert resp.status_code == 200
        [sent] = captured["messages"]
        assert sent == "what is 2 + 2?"

        events = _parse_sse(resp.content)
        full = _full_content(events)
        assert "4" in full
