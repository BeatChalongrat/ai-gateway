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
        user_msgs = [m for m in captured["messages"] if "john" in m or "<r0>" in m]
        assert len(user_msgs) >= 1
        sent = user_msgs[0]
        assert "john@acme.com" not in sent
        assert "<r0>" in sent


class TestStreamingPiiRestoredInResponse:
    def test_streaming_pii_restored_in_response(self, monkeypatch):
        # Adapter returns a reply containing the indexed token; the
        # public_chat restore step should rewrite it back to the
        # original value.
        _install_stream_spy(
            monkeypatch,
            chunks=["reply to ", "<r0>", " tomorrow"],
        )

        resp = _chat_stream("my email is john@acme.com, draft a reply")

        assert resp.status_code == 200
        events = _parse_sse(resp.content)
        full = _full_content(events)
        assert "john@acme.com" in full
        assert "<r0>" not in full


class TestStreamingBlockReturns403:
    def test_streaming_block_returns_403(self, monkeypatch):
        # Force strict profile so credential → BLOCK. Track A: the
        # streaming path now resolves the profile via the async variant,
        # so patch both names (sync kept for any non-async caller).
        monkeypatch.setattr(
            "app.api.routes.public_chat.get_runtime_profile",
            lambda _session, _pid: STRICT_PROFILE,
        )

        async def _async_strict(_session, _pid):
            return STRICT_PROFILE

        monkeypatch.setattr(
            "app.api.routes.public_chat.get_runtime_profile_async",
            _async_strict,
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
        # Provider-introduced PII is masked before the user sees it.
        # The orphan-redact pass (May 2026) strips non-inbound mask
        # tokens entirely so the user never sees a bare ``<rN>``
        # placeholder OR a ``[REDACTED]`` sentinel — the value is
        # already gone, no need to advertise the gap. The masking
        # guarantee — jane@example.com absent — is the load-bearing
        # check; the ``<r`` and ``[REDACTED]`` absence assertions
        # keep that contract pinned.
        assert "jane@example.com" not in full
        assert "<r" not in full
        assert "[REDACTED]" not in full


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


# ---------------------------------------------------------------------------
# Phase 11 Part D — credential head-hold guard.
#
# The rolling MIN_TAIL_CHARS (120) tail only protects patterns whose full
# match fits inside the retained window. A credential-class pattern LONGER
# than the tail (PEM key material, a long ``postgres://user:pass@...`` URL,
# a multi-KB JWT) could have its leading fragment emitted raw before the
# detector ever saw the completed match. These tests pin the guarantee that
# a boundary-split credential is never partially emitted, while normal
# prose and identity-class content keep streaming incrementally.
# ---------------------------------------------------------------------------

_PEM_KEY = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEpAIBAAKCAQEA7Zx9kQhtbW5uYmFzZTY0Ym9keWxpbmUxMjM0NTY3ODkwYWJj\n"
    "ZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXpBQkNERUZHSElKS0xNTk9QUVJTVFVWV1hZ\n"
    "WjAxMjM0NTY3ODkwYWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXo1NDMyMWZlZGNi\n"
    "-----END RSA PRIVATE KEY-----"
)

_LONG_CONN_STRING = (
    "postgresql://svc_reporting:sup3rS3cretPassw0rdWithLotsOfEntropy1234567890"
    "@db-primary.internal.example.com:5432/production_analytics_warehouse"
)


def _chunks_of(text: str, size: int) -> list[str]:
    """Split ``text`` into consecutive ``size``-char provider chunks."""
    return [text[i : i + size] for i in range(0, len(text), size)]


def _content_deltas(events: list[dict]) -> list[str]:
    """Each non-empty delta.content payload, in emission order."""
    out: list[str] = []
    for ev in events:
        delta = ev.get("choices", [{}])[0].get("delta", {})
        content = delta.get("content")
        if isinstance(content, str) and content:
            out.append(content)
    return out


def _leaked_fragments(
    deltas: list[str], secret: str, min_len: int = 8
) -> list[str]:
    """Return every ``min_len``-char slice of ``secret`` that appears
    inside ANY individual emitted chunk. Checking per-chunk (not just the
    concatenation) catches a partial head emitted before the full match
    formed — the exact leak shape this guard closes.
    """
    probes = {
        secret[i : i + min_len] for i in range(0, len(secret) - min_len + 1)
    }
    leaks: list[str] = []
    for d in deltas:
        for probe in probes:
            if probe in d:
                leaks.append(probe)
                break
    return leaks


class TestStreamingPemKeyNeverPartiallyEmitted:
    def test_pem_private_key_split_across_chunks_never_leaks(self, monkeypatch):
        """A >120-char PEM private key split into many small SSE chunks
        must never reach the client raw — not even a fragment. The
        private_key detector only matches the BEGIN header, so the base64
        body relies entirely on the streaming guard holding + dropping
        the key material.
        """
        assert len(_PEM_KEY) > 120
        prose_head = (
            "Sure — here is the generated deploy key for the staging box:\n"
        )
        prose_tail = (
            "\nStore it in your secrets manager and never commit it to the "
            "repository. Rotate it every 90 days per the security policy."
        )
        full = prose_head + _PEM_KEY + prose_tail
        _install_stream_spy(monkeypatch, chunks=_chunks_of(full, 13))

        resp = _chat_stream("give me the deploy key")

        assert resp.status_code == 200
        deltas = _content_deltas(_parse_sse(resp.content))
        leaks = _leaked_fragments(deltas, _PEM_KEY)
        assert not leaks, f"raw PEM fragments leaked: {leaks[:3]}"
        joined = "".join(deltas)
        # Key material is masked or absent — never raw.
        assert "MIIEpAIBAAKCAQEA" not in joined
        assert "PRIVATE KEY-----" not in joined
        # Surrounding prose still streams through.
        assert "deploy key for the staging box" in joined
        assert "Rotate it every 90 days" in joined


class TestStreamingLongConnStringNeverPartiallyEmitted:
    def test_long_db_connection_string_split_across_chunks_never_leaks(
        self, monkeypatch
    ):
        """A >120-char ``postgresql://user:pass@host/db`` URL split across
        small chunks: before the guard, the leading fragment exited the
        120-char tail (raw) before the ``@host`` part completed the match.
        No emitted chunk may carry any fragment of it.
        """
        conn = _LONG_CONN_STRING
        assert len(conn) > 120
        # Prose deliberately shares no 8-char substring with the secret,
        # so every probe hit is a genuine leak.
        prose_head = "Sure — connect using the following data source locator: "
        prose_tail = " — remember to rotate those credentials afterwards."
        full = prose_head + conn + prose_tail
        _install_stream_spy(monkeypatch, chunks=_chunks_of(full, 11))

        resp = _chat_stream("how do I connect to the data source?")

        assert resp.status_code == 200
        deltas = _content_deltas(_parse_sse(resp.content))
        leaks = _leaked_fragments(deltas, conn)
        assert not leaks, f"raw connection-string fragments leaked: {leaks[:3]}"
        joined = "".join(deltas)
        assert "sup3rS3cret" not in joined
        assert "db-primary.internal" not in joined
        # Surrounding prose still streams through.
        assert "data source locator" in joined
        assert "rotate those credentials" in joined


class TestStreamingProseStillIncremental:
    def test_prose_streams_incrementally_not_one_flush(self, monkeypatch):
        """The guard must not turn ordinary prose into full-response
        buffering — multiple content deltas must reach the wire, and the
        reassembled text must be byte-identical to the provider output.
        """
        prose = (
            "The quarterly report shows steady growth across all regions. "
            "Sales in the northern region rose by twelve percent, while the "
            "southern region held flat. Customer retention improved again "
            "and the support backlog dropped for the third month running."
        )
        _install_stream_spy(monkeypatch, chunks=_chunks_of(prose, 20))

        resp = _chat_stream("summarise the quarterly report")

        assert resp.status_code == 200
        deltas = _content_deltas(_parse_sse(resp.content))
        assert len(deltas) >= 2, (
            f"streaming collapsed into a single flush: {deltas!r}"
        )
        assert "".join(deltas) == prose


class TestStreamingIdentityClassLatencyUnchanged:
    def test_email_streams_and_masks_same_as_before(self, monkeypatch):
        """Identity-class content (an email) keeps the pre-guard behavior:
        masked via the normal 120-char rolling tail, no extra hold, and
        the stream stays incremental.
        """
        pre = (
            "Thanks for reaching out about the onboarding schedule. The best "
            "person to help you with the rollout details is our coordinator "
        )
        post = (
            " who replies within one business day. Feel free to mention this "
            "conversation when you write to her office anytime this week."
        )
        full = pre + "jane@example.com" + post
        _install_stream_spy(monkeypatch, chunks=_chunks_of(full, 20))

        resp = _chat_stream("who should I contact?")

        assert resp.status_code == 200
        deltas = _content_deltas(_parse_sse(resp.content))
        joined = "".join(deltas)
        # Same contract as TestStreamingProviderPiiMaskedUnderDefault:
        # value masked/absent, no bare token, no sentinel.
        assert "jane@example.com" not in joined
        assert "<r" not in joined
        assert "[REDACTED]" not in joined
        # Identity handling must not degrade streaming into one flush.
        assert len(deltas) >= 2
        assert "onboarding schedule" in joined
        assert "one business day" in joined
