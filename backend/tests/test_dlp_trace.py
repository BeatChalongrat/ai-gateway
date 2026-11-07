"""Migration 021 — DLP trace columns + admin surface tests.

The non-streaming + streaming paths capture four message snapshots
(``input_raw``, ``input_masked``, ``output_raw``, ``output_final``)
plus a JSON list of detection findings, which the admin Request
Detail endpoint exposes for audit. These tests exercise both paths
end-to-end through the integration TestClient (which stubs the
provider dispatcher to echo masked input back as the response, so
``output_raw`` ends up containing the indexed token before restore
swaps it for the original).
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.domain.dlp import (
    DetectionMatch,
    EnforcementAction,
    MatchAction,
    SensitivityLevel,
)
from app.domain.observability import (
    build_dlp_findings_json,
    extract_messages_text,
)
from app.main import app
from tests.conftest import _integration_engine


client = TestClient(app)
_HEADERS = {"Authorization": "Bearer demo-key"}


@pytest.fixture(autouse=True)
def _route_session_local_to_integration_engine(monkeypatch):
    """Make streaming's ``SessionLocal()`` write to the integration DB.

    The streaming path opens a fresh ``SessionLocal()`` inside
    ``event_generator`` because the dep-injected session is closed by
    the time the generator runs. Stock ``SessionLocal`` is bound to a
    different in-memory engine than the integration engine the test
    client uses, so without this patch the streamed row would land in
    a DB the admin GET never sees. ``test_streaming_routing.py`` uses
    the same trick.
    """
    integration_sessionmaker = sessionmaker(
        bind=_integration_engine, autoflush=False, autocommit=False
    )
    monkeypatch.setattr("app.core.db.SessionLocal", integration_sessionmaker)


def _parse_sse_events(body: bytes | str) -> list[dict]:
    if isinstance(body, bytes):
        body = body.decode()
    out: list[dict] = []
    for line in body.splitlines():
        if not line.startswith("data: "):
            continue
        data = line[6:].strip()
        if data == "[DONE]":
            continue
        out.append(json.loads(data))
    return out


def _request_id_from(response_json: dict) -> str:
    """Pick the request_id off a chat completion response."""
    rid = response_json.get("id")
    assert rid, f"no request id in response: {response_json}"
    return rid


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


class TestDlpFindingsHelper:
    def test_extract_messages_text_handles_strings(self):
        out = extract_messages_text([
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ])
        assert out == "hello\n---\nhi"

    def test_extract_messages_text_handles_blocks(self):
        out = extract_messages_text([
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "first"},
                    {"type": "image", "url": "ignored"},
                    {"type": "text", "text": "second"},
                ],
            }
        ])
        assert out == "first\n---\nsecond"

    def test_build_dlp_findings_json_returns_none_when_empty(self):
        assert build_dlp_findings_json([], [], {}) is None

    def test_build_dlp_findings_json_parses_correctly(self):
        match = DetectionMatch(
            detector_name="email",
            entity_type="email_address",
            category="pii",
            start=6,
            end=22,
            matched_text="alice@example.com",
            severity=SensitivityLevel.MEDIUM,
        )
        ma = MatchAction(
            match=match,
            action=EnforcementAction.MASK,
            reason_code="pii_mask",
            reason_message="PII masked",
        )
        span_map = {"<r0>": "alice@example.com"}

        raw = build_dlp_findings_json([ma], [], span_map)
        assert raw is not None

        findings = json.loads(raw)
        assert isinstance(findings, list) and len(findings) == 1

        f = findings[0]
        # Required field set per the migration spec.
        for key in (
            "direction",
            "detector",
            "entity_type",
            "category",
            "severity",
            "span_start",
            "span_end",
            "matched_text_len",
            "action",
            "mask_token",
        ):
            assert key in f, f"missing field {key}"

        assert f["detector"] == "email"
        assert f["entity_type"] == "email_address"
        assert f["category"] == "pii"
        assert f["severity"] == "MEDIUM"
        assert "matched_text" not in f
        assert f["matched_text_len"] == len("alice@example.com")
        assert f["action"] == "mask"
        assert f["mask_token"] == "<r0>"
        assert f["direction"] == "input"


# ---------------------------------------------------------------------------
# Non-streaming integration
# ---------------------------------------------------------------------------


class TestNonStreamingDlpTrace:
    def test_non_stream_request_logs_dlp_trace(self):
        # Send PII through the gateway. The stub dispatcher echoes the
        # *masked* last message back as the response content, so the
        # captured output_raw contains the ``<rN>`` token; restore puts
        # the original email back in the reply the caller gets, and
        # nothing persisted holds it.
        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={
                "model": "auto",
                "messages": [
                    {"role": "user", "content": "Email me at alice@example.com please"}
                ],
                "metadata": {},
            },
        )
        assert resp.status_code == 200, resp.text
        request_id = _request_id_from(resp.json())
        # The caller gets their own email back (restore).
        assert "alice@example.com" in resp.json()["choices"][0]["message"]["content"]

        detail = client.get(f"/admin/requests/{request_id}", headers=_HEADERS)
        assert detail.status_code == 200, detail.text
        body = detail.json()

        # Snapshot 1: the raw input is not persisted in clear — a fixed
        # placeholder (the verbatim lives in ``input_raw_encrypted``).
        assert body["input_raw"] == "[MASKED_BY_DLP]"

        # Snapshot 2: masked input swaps the email for an indexed token.
        assert body["input_masked"] is not None
        assert "alice@example.com" not in body["input_masked"]
        assert re.search(r"Email me at <r\d+> please", body["input_masked"])

        # Snapshot 3 & 4: output_raw is captured BEFORE restore, so the
        # indexed token survives there. output_final holds the same
        # provider-safe (masked) text — never the restored reply the
        # caller saw.
        assert re.search(r"<r\d+>", body["output_raw"])
        assert "alice@example.com" not in body["output_raw"]
        assert re.search(r"<r\d+>", body["output_final"])
        assert "alice@example.com" not in body["output_final"]

        # At least one email finding in the parsed JSON list.
        findings = body["dlp_findings"]
        assert findings is not None
        assert any(f["detector"] == "email" for f in findings)


# ---------------------------------------------------------------------------
# Streaming integration
# ---------------------------------------------------------------------------


class TestStreamingDlpTrace:
    def test_stream_request_logs_dlp_trace(self, monkeypatch):
        # The default streaming stub in conftest yields a fixed chunk
        # that doesn't echo the input, so the masked indexed token
        # never reaches output_raw and there's nothing for restore to
        # swap. Override OpenAIAdapter.stream_chat for this test to
        # echo the (already-masked) last user message — that way the
        # restore step actually does work and the streamed answer ends
        # up with the original PII back.
        from app.domain.providers import ProviderStreamChunk
        from app.services.providers.openai_adapter import OpenAIAdapter

        async def _echo_stream(self, payload, messages, *, resolved_model=None, api_key=None):
            last = messages[-1].content if messages else ""
            yield ProviderStreamChunk(
                delta=f"You said: {last}",
                finish_reason="stop",
                model=resolved_model or "gpt-4o-mini",
            )

        monkeypatch.setattr(OpenAIAdapter, "stream_chat", _echo_stream)

        # The streaming path runs DLP mask + buffered restore in
        # event_generator. After the stream completes the row is
        # persisted via SessionLocal, so we can fetch it via the same
        # admin endpoint.
        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={
                "model": "gpt-4o-mini",
                "stream": True,
                "messages": [
                    {"role": "user", "content": "ping bob@example.org for status"}
                ],
                "metadata": {},
            },
        )
        assert resp.status_code == 200, resp.text

        # Parse the SSE body. Reading resp.content also drains the
        # generator, so the persist block at the end of event_generator
        # runs. Typed frames (``dlp_notice``, ``model_resolved``) come
        # first and carry no id; the ``chat.completion.chunk`` frames
        # carry the gateway request id.
        events = _parse_sse_events(resp.content)
        assert events, "no SSE events in stream response"
        chunks = [e for e in events if e.get("object") == "chat.completion.chunk"]
        assert chunks, "no content chunk in stream response"
        request_id = chunks[0]["id"]
        # Stream restore must put the original email back into the answer
        # the caller reads.
        streamed = "".join(
            c["choices"][0]["delta"].get("content") or "" for c in chunks if c.get("choices")
        )
        assert "bob@example.org" in streamed

        detail = client.get(f"/admin/requests/{request_id}", headers=_HEADERS)
        assert detail.status_code == 200, detail.text
        body = detail.json()

        # Persisted: a placeholder for the raw input, token-only text for
        # the other three stages — the email itself in none of them.
        assert body["input_raw"] == "[MASKED_BY_DLP]"
        assert body["input_masked"] is not None
        assert "bob@example.org" not in body["input_masked"]
        assert re.search(r"ping <r\d+> for status", body["input_masked"])

        assert re.search(r"<r\d+>", body["output_raw"])
        assert "bob@example.org" not in body["output_raw"]
        assert re.search(r"<r\d+>", body["output_final"])
        assert "bob@example.org" not in body["output_final"]

        findings = body["dlp_findings"]
        assert findings is not None
        assert any(f["detector"] == "email" for f in findings)


# ---------------------------------------------------------------------------
# Clean (no PII) request
# ---------------------------------------------------------------------------


class TestCleanRequestTrace:
    def test_clean_request_has_null_trace_or_empty_findings(self):
        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "what is the capital of france?"}],
                "metadata": {},
            },
        )
        assert resp.status_code == 200
        request_id = _request_id_from(resp.json())

        detail = client.get(f"/admin/requests/{request_id}", headers=_HEADERS)
        assert detail.status_code == 200
        body = detail.json()

        # Nothing was masked, so stage 2 (what the provider was sent) is
        # the question as typed; stage 1 holds only the marker that points
        # at the encrypted copy, never the text.
        assert body["input_masked"] == "what is the capital of france?"
        assert body["input_raw"] == "[NOT_PERSISTED_SEE_ENCRYPTED]"
        # No findings either way: parsed list is None when the JSON
        # blob was None on the row.
        assert body["dlp_findings"] in (None, [])
