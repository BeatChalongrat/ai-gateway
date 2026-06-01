"""Phase C — streaming provider errors surface as an SSE ``error`` frame.

Before this fix, a provider failure that happened AFTER the streaming
response committed its 200 status line (e.g. an OpenRouter 401 raised
inside ``stream_chat``) propagated out of the generator and closed the
stream with an EMPTY body — the web client rendered a blank assistant
bubble. Now the generator emits a typed ``error`` frame + ``[DONE]`` so
the client can show an error state with a reason.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.api.routes.public_chat import _classify_stream_error
from app.core.exceptions import (
    ProviderError,
    ProviderTimeoutError,
    RateLimitExceededError,
    UpstreamBadResponseError,
)
from app.domain.providers import ProviderStreamChunk
from app.main import app
from app.services.providers.anthropic_adapter import AnthropicAdapter

client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"Authorization": "Bearer demo-key"}


def _parse_sse(body: str) -> list[dict]:
    frames = []
    for line in body.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[len("data: ") :]
        if payload == "[DONE]":
            continue
        try:
            frames.append(json.loads(payload))
        except json.JSONDecodeError:
            pass
    return frames


class TestClassifyStreamError:
    def test_auth_failure_maps_to_provider_auth_failed(self):
        exc = ProviderError("rejected", details={"status": 401})
        code, msg = _classify_stream_error(exc)
        assert code == "provider_auth_failed"
        assert msg

    def test_403_also_auth_failed(self):
        code, _ = _classify_stream_error(ProviderError("x", details={"status": 403}))
        assert code == "provider_auth_failed"

    def test_rate_limit_maps(self):
        code, _ = _classify_stream_error(RateLimitExceededError("slow down"))
        assert code == "rate_limited"

    def test_timeout_maps(self):
        code, _ = _classify_stream_error(ProviderTimeoutError("timed out"))
        assert code == "provider_timeout"

    def test_generic_provider_error(self):
        code, _ = _classify_stream_error(ProviderError("boom"))
        assert code == "provider_error"


class TestStreamErrorFrame:
    def test_unrecoverable_provider_error_emits_error_frame(self, monkeypatch):
        """A non-eligible, non-auth error raised before the first chunk
        must yield an ``error`` frame + ``[DONE]`` (no silent empty body)."""

        async def _boom(self, payload, messages, *, resolved_model=None, api_key=None):
            raise UpstreamBadResponseError(
                "Provider rejected request (400).",
                details={"provider": "anthropic", "status": 400},
            )
            yield  # pragma: no cover — makes this an async generator

        monkeypatch.setattr(AnthropicAdapter, "stream_chat", _boom)

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

        frames = _parse_sse(body)
        error_frames = [f for f in frames if f.get("object") == "error"]
        assert len(error_frames) == 1, body
        err = error_frames[0]["error"]
        assert err["code"]  # a non-empty machine code
        assert err["message"]
        # No content delta should have been emitted on a pre-yield failure.
        content_frames = [
            f
            for f in frames
            if f.get("choices") and f["choices"][0].get("delta", {}).get("content")
        ]
        assert content_frames == []

    def test_successful_stream_has_no_error_frame(self, monkeypatch):
        """Guard: the error path must not fire on a healthy stream."""

        async def _ok(self, payload, messages, *, resolved_model=None, api_key=None):
            yield ProviderStreamChunk(
                delta="hello", finish_reason="stop", model=resolved_model or "claude"
            )

        monkeypatch.setattr(AnthropicAdapter, "stream_chat", _ok)

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
        frames = _parse_sse(body)
        assert [f for f in frames if f.get("object") == "error"] == []
