"""Moderation tests for the streaming chat completions path.

Mirrors the non-streaming moderation tests but exercises the SSE
endpoint in ``public_chat.py``. Each test monkeypatches three things:

  1. ``OpenAIAdapter.stream_chat`` → a spy that records whether the
     provider was reached (used to assert the moderation block kicks
     in BEFORE the provider is called when content is flagged).
  2. ``ModerationService.check`` → a stub that returns a controllable
     ``ModerationResult`` so we can test each branch without hitting
     the real /v1/moderations endpoint.
  3. ``app.api.routes.public_chat.get_profile`` → returns a profile
     with the desired ``moderation_enabled`` flag.
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
from app.services.moderation_service import ModerationResult, ModerationService
from app.services.policy.profiles import DEFAULT_PROFILE, STRICT_PROFILE
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


def _install_stream_spy(monkeypatch, chunks: list[str] | None = None) -> dict:
    """Replace OpenAIAdapter.stream_chat with a capturing async generator."""
    chunks = chunks or ["stream ok"]
    captured: dict = {"called": False}

    async def _spy(
        self,
        payload,
        messages,
        *,
        resolved_model: str | None = None,
        api_key: str | None = None,
    ) -> AsyncIterator[ProviderStreamChunk]:
        captured["called"] = True
        for i, text in enumerate(chunks):
            is_last = i == len(chunks) - 1
            yield ProviderStreamChunk(
                delta=text,
                finish_reason="stop" if is_last else None,
                model=resolved_model or "gpt-4o-mini",
            )

    monkeypatch.setattr(OpenAIAdapter, "stream_chat", _spy)
    return captured


def _install_moderation_stub(monkeypatch, result: ModerationResult) -> dict:
    """Replace ModerationService.check with a stub returning *result*."""
    captured: dict = {"called": False, "text": None}

    async def _stub(self, text: str, api_key: str) -> ModerationResult:
        captured["called"] = True
        captured["text"] = text
        return result

    monkeypatch.setattr(ModerationService, "check", _stub)
    return captured


def _force_profile(monkeypatch, profile):
    monkeypatch.setattr(
        "app.api.routes.public_chat.get_profile", lambda _pid: profile
    )


def _log_for(request_id: str) -> RequestLogORM | None:
    Session = sessionmaker(bind=_integration_engine)
    s = Session()
    try:
        return s.get(RequestLogORM, request_id)
    finally:
        s.close()


class TestStreamingModerationSkippedWhenDisabled:
    def test_streaming_moderation_skipped_when_disabled(self, monkeypatch):
        # DEFAULT_PROFILE has moderation_enabled=False.
        _force_profile(monkeypatch, DEFAULT_PROFILE)
        # Even if check *would* flag, it should never be called.
        mod_spy = _install_moderation_stub(
            monkeypatch,
            ModerationResult(flagged=True, categories={"violence": True}),
        )
        stream_spy = _install_stream_spy(monkeypatch, chunks=["clean reply"])

        resp = _chat_stream("tell me a joke")

        assert resp.status_code == 200
        assert mod_spy["called"] is False
        assert stream_spy["called"] is True


class TestStreamingModerationBlocksFlaggedContent:
    def test_streaming_moderation_blocks_flagged_content(self, monkeypatch):
        _force_profile(monkeypatch, STRICT_PROFILE)
        mod_spy = _install_moderation_stub(
            monkeypatch,
            ModerationResult(
                flagged=True,
                categories={"violence": True},
                scores={"violence": 0.95},
            ),
        )
        stream_spy = _install_stream_spy(monkeypatch)

        resp = _chat_stream("some fake harmful thing")

        assert resp.status_code == 403
        body = resp.json()
        assert body["error"]["code"] == "moderation_flagged"
        assert mod_spy["called"] is True
        assert stream_spy["called"] is False

        # The request_log row is keyed on the X-Request-Id header —
        # but on a 403 the stream never runs, so we instead look up the
        # most recent blocked row for this test's predicate.
        # ``policy_action='block'`` aligns with the dashboard
        # (admin-ui/app/requests/page.tsx:190) and dlp_block_spike
        # alert filters; the *kind* of block (moderation vs DLP vs
        # guardrail) is preserved on ``error_code``.
        Session = sessionmaker(bind=_integration_engine)
        s = Session()
        try:
            row = (
                s.query(RequestLogORM)
                .filter(RequestLogORM.error_code == "moderation_flagged")
                .order_by(RequestLogORM.created_at.desc())
                .first()
            )
        finally:
            s.close()
        assert row is not None
        assert row.status == "blocked"
        assert row.policy_action == "block"
        assert row.error_code == "moderation_flagged"


class TestStreamingModerationPassesCleanContent:
    def test_streaming_moderation_passes_clean_content(self, monkeypatch):
        _force_profile(monkeypatch, STRICT_PROFILE)
        mod_spy = _install_moderation_stub(
            monkeypatch, ModerationResult(flagged=False)
        )
        stream_spy = _install_stream_spy(monkeypatch, chunks=["safe reply"])

        resp = _chat_stream("what is 2 + 2?")

        assert resp.status_code == 200
        assert mod_spy["called"] is True
        assert stream_spy["called"] is True

        # Body should carry the stub reply as one SSE chunk.
        events = [
            json.loads(line[6:])
            for line in resp.content.decode().splitlines()
            if line.startswith("data: ") and not line.endswith("[DONE]")
        ]
        content = events[0]["choices"][0]["delta"]["content"]
        assert "safe reply" in content


class TestStreamingModerationFailOpenOnError:
    def test_streaming_moderation_fail_open_on_error(self, monkeypatch):
        _force_profile(monkeypatch, STRICT_PROFILE)
        mod_spy = _install_moderation_stub(
            monkeypatch,
            ModerationResult(flagged=False, error="timeout: upstream"),
        )
        stream_spy = _install_stream_spy(monkeypatch, chunks=["still delivered"])

        resp = _chat_stream("what is the weather today?")

        # Fail-open: the error field is set but flagged=False, so the
        # stream must still complete normally.
        assert resp.status_code == 200
        assert mod_spy["called"] is True
        assert stream_spy["called"] is True
