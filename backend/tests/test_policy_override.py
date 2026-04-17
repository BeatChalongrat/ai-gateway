"""Per-request policy_profile_id override tests.

Exercises the new optional ``policy_profile_id`` field on
``ChatCompletionsRequest``. Field validation (422 on unknown names)
happens in ``schemas/public.py``; project-scope permission (403 on
escalation attempts) happens in ``project_service.resolve_project_context``
via ``is_override_permitted``; the effective profile is persisted on
``request_logs.policy_profile_id`` for audit.

These tests assume the integration test client (``conftest._integration_engine``)
and the standard ``demo-key`` header.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.domain.providers import ProviderStreamChunk
from app.main import app
from app.models.project import ProjectORM
from app.models.request_log import RequestLogORM
from app.services.providers.openai_adapter import OpenAIAdapter
from tests.conftest import _integration_engine


client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"Authorization": "Bearer demo-key"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _set_project_profile(profile_id: str | None) -> str:
    """Set the integration test project's policy profile, return prior value."""
    Session = sessionmaker(bind=_integration_engine)
    s = Session()
    try:
        proj = s.get(ProjectORM, "test-project")
        prior = proj.policy_profile_id
        proj.policy_profile_id = profile_id
        s.commit()
        return prior
    finally:
        s.close()


@pytest.fixture
def restore_default_profile():
    """Ensure each test restores the project profile to 'default' afterwards."""
    yield
    _set_project_profile("default")


def _log_for(request_id: str) -> RequestLogORM | None:
    Session = sessionmaker(bind=_integration_engine)
    s = Session()
    try:
        return s.get(RequestLogORM, request_id)
    finally:
        s.close()


def _chat(content: str, **extra):
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": content}],
        "metadata": {},
    }
    body.update(extra)
    return client.post(
        "/v1/chat/completions", headers=_HEADERS, json=body
    )


def _install_stream_spy(monkeypatch, chunks: list[str]):
    captured: dict = {"called": False, "messages": None}

    async def _spy(
        self,
        payload,
        messages,
        *,
        resolved_model: str | None = None,
        api_key: str | None = None,
    ) -> AsyncIterator[ProviderStreamChunk]:
        captured["called"] = True
        captured["messages"] = [m.content for m in messages]
        for i, text in enumerate(chunks):
            is_last = i == len(chunks) - 1
            yield ProviderStreamChunk(
                delta=text,
                finish_reason="stop" if is_last else None,
                model=resolved_model or "gpt-4o-mini",
            )

    monkeypatch.setattr(OpenAIAdapter, "stream_chat", _spy)
    return captured


@pytest.fixture(autouse=True)
def _route_session_local_to_integration_engine(monkeypatch):
    """Make streaming's `SessionLocal()` land rows in the integration DB.

    Mirrors the fixture in test_streaming_routing.py — needed for the
    streaming override tests below that look up the persisted log row
    via ``_log_for``.
    """
    integration_sessionmaker = sessionmaker(
        bind=_integration_engine, autoflush=False, autocommit=False
    )
    monkeypatch.setattr(
        "app.core.db.SessionLocal", integration_sessionmaker
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNoOverrideUsesProjectProfile:
    def test_no_override_uses_project_profile(self, restore_default_profile):
        _set_project_profile("default")
        resp = _chat("hello world")
        assert resp.status_code == 200
        body = resp.json()
        row = _log_for(body["id"])
        assert row is not None
        assert row.policy_profile_id == "default"


class TestOverrideToStrictAccepted:
    def test_override_to_strict_accepted(self, restore_default_profile):
        _set_project_profile("default")
        resp = _chat("hello world", policy_profile_id="strict")
        assert resp.status_code == 200
        body = resp.json()
        row = _log_for(body["id"])
        assert row is not None
        assert row.policy_profile_id == "strict"


class TestOverrideToDefaultRejectedWhenProjectIsStrict:
    def test_override_to_default_rejected_when_project_is_strict(
        self, monkeypatch, restore_default_profile
    ):
        _set_project_profile("strict")
        # Spy the dispatcher to prove the provider is never reached.
        called: dict = {"count": 0}

        async def _boom_dispatch(self, route, payload, messages, api_key=None):
            called["count"] += 1
            from app.domain.providers import ProviderResponse
            return ProviderResponse(content="unreachable", model=route.model_name)

        monkeypatch.setattr(
            "app.services.provider_dispatcher.ProviderDispatcher.dispatch",
            _boom_dispatch,
        )

        resp = _chat("hello", policy_profile_id="default")
        assert resp.status_code == 403
        body = resp.json()
        assert body["error"]["code"] == "authorization_error"
        assert "not permitted" in body["error"]["message"].lower()
        assert called["count"] == 0


class TestUnknownProfileReturns422:
    def test_unknown_profile_returns_422(self):
        resp = _chat("hello", policy_profile_id="nonexistent_profile")
        assert resp.status_code == 422
        body = resp.json()
        # Pydantic wraps ValueError in a 422 with a validation_error shape.
        # Look for the error text in the serialized body (Pydantic v2).
        text = json.dumps(body).lower()
        assert "unknown policy profile" in text or "nonexistent_profile" in text


class TestOverridePersistedInRequestLog:
    def test_override_persisted_in_request_log(self, restore_default_profile):
        _set_project_profile("default")
        resp = _chat("hello", policy_profile_id="strict")
        assert resp.status_code == 200
        rid = resp.json()["id"]

        # GET /admin/requests/{id} should echo the override.
        detail = client.get(
            f"/admin/requests/{rid}", headers=_HEADERS
        )
        assert detail.status_code == 200
        assert detail.json()["policy_profile_id"] == "strict"


class TestStreamingOverrideToStrictAccepted:
    def test_streaming_override_to_strict_accepted(
        self, monkeypatch, restore_default_profile
    ):
        _set_project_profile("default")
        stream_spy = _install_stream_spy(monkeypatch, chunks=["nope"])

        # Credential under "default" profile is MASK (request succeeds).
        # Under "strict" it's BLOCK → 403 before the stream starts.
        key = "sk-ant-api03-" + ("a" * 40)
        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": f"my key is {key}"}],
                "metadata": {},
                "stream": True,
                "policy_profile_id": "strict",
            },
        )
        assert resp.status_code == 403
        body = resp.json()
        assert body["error"]["code"] == "dlp_violation"
        assert stream_spy["called"] is False


class TestStreamingProfilePersistedInLog:
    def test_streaming_profile_persisted_in_log(
        self, monkeypatch, restore_default_profile
    ):
        _set_project_profile("default")
        _install_stream_spy(monkeypatch, chunks=["hello back"])

        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hello"}],
                "metadata": {},
                "stream": True,
                "policy_profile_id": "strict",
            },
        )
        assert resp.status_code == 200

        # Parse the first SSE event to get the request_id.
        first_line = next(
            ln for ln in resp.content.decode().splitlines()
            if ln.startswith("data: ") and "[DONE]" not in ln
        )
        first = json.loads(first_line[6:])
        row = _log_for(first["id"])
        assert row is not None
        assert row.policy_profile_id == "strict"
