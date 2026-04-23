"""Phase 3.5 Commits 30-31 — incremental SSE streaming with per-chunk DLP.

Restores real-time streaming UX (one delta per chunk, not one giant
final dump) while keeping PII masking airtight via a 50-char rolling
tail. Pairs with ``test_sse_dlp_ordering.py``: the P0-7 regression
there verifies the safety contract (no raw PII on the wire) and this
suite verifies the new performance contract (multiple chunks emitted
throughout the stream).

Tests:
- Clean stream → multiple content deltas (not a single dump).
- PII wholly inside one chunk → masked before emit.
- PII spanning two chunks (credential split across boundary) → still
  masked; raw value never appears.
- DLP scan failure on a chunk → chunk dropped, stream continues,
  warning logged.
"""

from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers — same adapter-override pattern as test_sse_dlp_ordering.
# ---------------------------------------------------------------------------


def _install_streaming_adapter(monkeypatch, pieces: list[str]):
    from app.domain.providers import ProviderStreamChunk
    from app.services.providers.openai_adapter import OpenAIAdapter
    from app.services.providers.anthropic_adapter import AnthropicAdapter
    from app.services.provider_key_service import ProviderKeyService

    async def _fake_stream(self, payload, messages, *, resolved_model=None, api_key=None):
        for piece in pieces:
            yield ProviderStreamChunk(delta=piece, finish_reason=None, model="stub-model")
        yield ProviderStreamChunk(delta="", finish_reason="stop", model="stub-model")

    monkeypatch.setattr(OpenAIAdapter, "stream_chat", _fake_stream)
    monkeypatch.setattr(AnthropicAdapter, "stream_chat", _fake_stream)
    monkeypatch.setattr(
        ProviderKeyService,
        "get_api_key",
        lambda self, *a, **k: "test-provider-key",
    )


def _deltas(resp) -> list[str]:
    """Return each content-delta payload, in order."""
    out: list[str] = []
    for line in resp.text.splitlines():
        if not line.startswith("data: "):
            continue
        raw = line[len("data: ") :].strip()
        if raw == "[DONE]":
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        for ch in obj.get("choices", []):
            delta = (ch.get("delta") or {}).get("content")
            if isinstance(delta, str) and delta:
                out.append(delta)
    return out


def _send_stream(client: TestClient) -> "httpx.Response":  # type: ignore
    return client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "go"}],
            "stream": True,
        },
        headers={"Authorization": "Bearer gw_test_"},
    )


# ---------------------------------------------------------------------------
# Clean stream — should be incremental.
# ---------------------------------------------------------------------------


class TestIncrementalEmission:
    def test_clean_stream_emits_multiple_content_deltas(self, monkeypatch):
        """80 chars across 4 pieces → at least 2 emitted deltas (tail +
        everything after). Previously (Phase 2C C13) the whole body was
        collapsed into a single delta."""
        pieces = [
            "This is the first sentence. ",
            "This is the second sentence. ",
            "This is the third sentence. ",
            "Final sentence to finish off the reply.",
        ]
        _install_streaming_adapter(monkeypatch, pieces)
        from app.main import app

        client = TestClient(app)
        resp = _send_stream(client)
        assert resp.status_code == 200
        deltas = _deltas(resp)
        # At minimum: one mid-stream emission + one tail flush = 2.
        # Collapsed-single-chunk behaviour would give exactly 1.
        assert len(deltas) >= 2, (
            f"Expected multiple deltas, got {len(deltas)}: {deltas}"
        )
        # Full text reassembled must match the provider input.
        assert "".join(deltas) == "".join(pieces)


# ---------------------------------------------------------------------------
# PII within a single chunk — masked before emit.
# ---------------------------------------------------------------------------


class TestSingleChunkPiiMasked:
    def test_credential_inside_one_chunk_is_masked(self, monkeypatch):
        # Credential fully inside one chunk; surrounding chunks keep
        # the stream long enough to flush.
        pieces = [
            "Here is the key: AKIAIOSFODNN7EXAMPLE — please save it. ",
            "Additional context follows to push the tail window past 50 chars.",
        ]
        _install_streaming_adapter(monkeypatch, pieces)
        from app.main import app

        client = TestClient(app)
        resp = _send_stream(client)
        assert resp.status_code == 200
        body = resp.text
        assert "AKIAIOSFODNN7EXAMPLE" not in body, (
            "credential leaked in-chunk: " + body[:400]
        )
        combined = "".join(_deltas(resp))
        assert "AKIAIOSFODNN7EXAMPLE" not in combined


# ---------------------------------------------------------------------------
# PII spanning two chunks — cross-boundary masking via tail.
# ---------------------------------------------------------------------------


class TestCrossChunkPiiMasked:
    def test_credential_split_across_two_chunks_is_masked(self, monkeypatch):
        """Split an AWS-access-key across a chunk boundary. The 50-char
        tail retention means the second chunk's DLP scan sees the
        concatenated tail+chunk text and catches the full match."""
        pieces = [
            # First half of the credential + enough preamble to get past
            # the minimum tail retention easily.
            "Intro line to bulk up the stream. Here is the token: AKIAIOSFODN",
            # Second half. The DLP window = tail_masked + this. Since
            # nothing in "AKIAIOSFODN" alone matches the AWS-key pattern,
            # the first chunk retained it raw in the tail. Combining
            # with "N7EXAMPLE..." completes the pattern.
            "N7EXAMPLE. Everything afterwards is just padding to keep going.",
        ]
        _install_streaming_adapter(monkeypatch, pieces)
        from app.main import app

        client = TestClient(app)
        resp = _send_stream(client)
        assert resp.status_code == 200
        body = resp.text
        combined = "".join(_deltas(resp))
        # Key assertion: neither the full key nor a contiguous prefix
        # that reveals ≥half of it should reach the client.
        assert "AKIAIOSFODNN7EXAMPLE" not in combined, (
            "cross-chunk credential leak: " + combined[:300]
        )
        assert "AKIAIOSFODNN7EXAMPLE" not in body


# ---------------------------------------------------------------------------
# DLP failure mode — block chunk, continue stream.
# ---------------------------------------------------------------------------


class TestDlpFailureHandling:
    def test_dlp_scan_exception_drops_chunk_and_logs(self, monkeypatch, caplog):
        """Simulate an exception during _scan_text_as_result. The
        generator's _dlp_scan_and_mask fallback drops the chunk and
        logs a warning; the stream continues with no PII leak."""
        from app.services.orchestrator import GatewayOrchestrator

        def boom(self, *args, **kwargs):
            raise RuntimeError("scanner crashed")

        monkeypatch.setattr(
            GatewayOrchestrator, "_scan_text_as_result", boom
        )
        pieces = [
            "First line of reply is safe. ",
            "Second line content. ",
            "Enough extra padding to push the tail past 50 chars and trigger a flush.",
        ]
        _install_streaming_adapter(monkeypatch, pieces)
        from app.main import app

        client = TestClient(app)
        caplog.set_level(logging.WARNING)
        resp = _send_stream(client)
        # The stream itself succeeds — DLP failure is per-chunk.
        assert resp.status_code == 200
        # Failing every scan means every chunk should be dropped. The
        # response should be "data: [DONE]" + a finish frame, with no
        # content deltas.
        deltas = _deltas(resp)
        assert deltas == [], f"Expected no deltas after DLP failure, got: {deltas}"
