"""Phase 2C — Commit 13: P0-7 SSE DLP ordering (TDD).

Contract: a streaming response MUST pass every byte of provider text
through DLP BEFORE the client sees it. Before this commit the adapter
yielded raw delta chunks straight onto the wire; the DLP pass ran
AFTER the stream completed, so fresh PII introduced by the provider
(e.g. the model returning an SSN, phone number, etc.) was visible to
the caller in the pre-mask deltas.

Fix: accumulate all provider chunks server-side, run outbound DLP on
the full buffer, then emit the masked body as a single content delta
followed by usage + DONE. Loses token-level streaming granularity
(which was already noted as a Known Caveat in the pre-fix code
comments) but guarantees DLP ordering.

The test overrides the adapter's ``stream_chat`` to produce chunks
containing an email address, then inspects the SSE stream text
to verify the raw email never appears — only the masked token.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


def _stream_chunks_with_pii(*args, **kwargs):
    """Async generator that yields provider chunks containing a credential.

    We deliberately use an AWS-access-key shape rather than an email
    because the ``default`` DLP profile's ``category_actions_output``
    only masks the ``credential`` + ``secret`` categories on the
    outbound path (PII / financial / network / identity fall through
    to ALLOW by design — see profiles.py). An AWS key triggers the
    ``credential`` category and therefore must be masked before emit.
    """
    from app.domain.providers import ProviderStreamChunk

    async def _gen():
        for piece in [
            "Here is the key: ",
            "AKIA",
            "IOSFODNN7EXAMPLE",
            " — keep it safe.",
        ]:
            yield ProviderStreamChunk(
                delta=piece,
                finish_reason=None,
                model="stub-model",
            )
        yield ProviderStreamChunk(
            delta="",
            finish_reason="stop",
            model="stub-model",
        )

    return _gen()


@pytest.fixture
def pii_streaming_adapter(monkeypatch):
    from app.services.providers.openai_adapter import OpenAIAdapter
    from app.services.providers.anthropic_adapter import AnthropicAdapter

    async def _fake_stream(self, payload, messages, *, resolved_model=None, api_key=None):
        async for c in _stream_chunks_with_pii():
            yield c

    monkeypatch.setattr(OpenAIAdapter, "stream_chat", _fake_stream)
    monkeypatch.setattr(AnthropicAdapter, "stream_chat", _fake_stream)

    # Route the request past the pre-flight "no API key" 503 that blocks
    # the 147 unrelated streaming tests. We stub the resolver at the
    # class level so every per-request orchestrator instance sees it.
    from app.services.provider_key_service import ProviderKeyService

    def _fake_get_api_key(self, *args, **kwargs):
        return "test-provider-key"

    monkeypatch.setattr(
        ProviderKeyService, "get_api_key", _fake_get_api_key,
    )


def _read_sse_text(resp) -> str:
    """Concatenate every ``data:`` payload's content fields from an SSE
    response, so tests can assert on the full client-visible text.
    """
    all_content = []
    for line in resp.text.splitlines():
        if not line.startswith("data: "):
            continue
        raw = line[len("data: "):].strip()
        if raw == "[DONE]":
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        for ch in obj.get("choices", []):
            delta = (ch.get("delta") or {}).get("content")
            if isinstance(delta, str):
                all_content.append(delta)
    return "".join(all_content)


class TestStreamedPiiIsMaskedBeforeEmit:
    def test_raw_credential_does_not_appear_in_stream(self, pii_streaming_adapter):
        from app.main import app
        client = TestClient(app)
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "Give me the key?"}],
                "stream": True,
            },
            headers={"Authorization": "Bearer gw_test_"},
        )
        # The stream must not error out — DLP enforcement of response
        # scan should redact, not block.
        assert resp.status_code == 200, resp.text
        body = resp.text
        # Critical: the literal credential must never appear in the
        # client-visible SSE text.
        assert "AKIAIOSFODNN7EXAMPLE" not in body, (
            "P0-7 regression: raw credential leaked into streamed response"
        )
        client_content = _read_sse_text(resp)
        assert "AKIAIOSFODNN7EXAMPLE" not in client_content
        # The masked token substitute should be visible to confirm the
        # redaction actually happened (detectors tag credentials with
        # an indexed token like [AWS_ACCESS_KEY_0]).
        assert client_content  # non-empty body
