"""End-to-end tests for the streaming router chain.

Asserts that the streaming path runs the same router chain as the
non-streaming path: semantic > keyword (DB + default) > service.
Also asserts that DLP masking runs BEFORE the router chain, so the
semantic router's embedding call never sees raw PII.

All tests stub ``OpenAIAdapter.stream_chat`` so no real provider
calls are made. A shared fixture routes
``app.core.db.SessionLocal`` at the ``public_chat`` import site to
the integration engine, so the row persisted by ``event_generator``
is visible to ``_log_for``.
"""

from __future__ import annotations

import json
import uuid
from typing import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.domain.providers import ProviderStreamChunk
from app.main import app
from app.models.keyword_rule import KeywordRuleORM
from app.models.request_log import RequestLogORM
from app.services.providers.openai_adapter import OpenAIAdapter
from app.services.semantic_router import SemanticRouterDisabled
from tests.conftest import _integration_engine


client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"Authorization": "Bearer demo-key"}


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _route_session_local_to_integration_engine(monkeypatch):
    """Make streaming's `SessionLocal()` land rows in the integration DB.

    ``public_chat.event_generator`` opens its own ``SessionLocal()`` to
    persist the request_log row after the stream completes (the
    dep-injected session is closed by then). The stock ``SessionLocal``
    is bound to ``app.core.db.engine`` — a different in-memory engine
    than the ``_integration_engine`` used by the FastAPI test client —
    so tests would otherwise never see the streaming row.

    Patching at ``app.core.db.SessionLocal`` works because the import
    in ``public_chat.py`` is ``from app.core.db import SessionLocal``
    executed INSIDE ``event_generator`` each run, so it picks up the
    patched reference.
    """
    integration_sessionmaker = sessionmaker(
        bind=_integration_engine, autoflush=False, autocommit=False
    )
    monkeypatch.setattr(
        "app.core.db.SessionLocal", integration_sessionmaker
    )


@pytest.fixture(autouse=True)
def _cleanup_keyword_rules():
    """Delete any keyword rules this test created once it exits.

    ``_integration_engine`` is a module-level singleton shared across
    tests, so rows leak between tests unless we clean up. Rules that
    survive would pollute the `service` / `keyword` router-used
    assertions in later tests.
    """
    yield
    Session = sessionmaker(bind=_integration_engine)
    s = Session()
    try:
        s.query(KeywordRuleORM).delete()
        s.commit()
    finally:
        s.close()


def _insert_keyword_rule(
    *,
    name: str,
    keywords: list[str],
    model_override: str,
    scope: str = "global",
    scope_id: str | None = None,
    priority: int = 0,
    enabled: bool = True,
) -> str:
    """Insert a KeywordRuleORM row and return its generated id."""
    Session = sessionmaker(bind=_integration_engine)
    s = Session()
    try:
        row = KeywordRuleORM(
            id=str(uuid.uuid4()),
            name=name,
            keywords_json=json.dumps(keywords),
            model_override=model_override,
            priority=priority,
            scope=scope,
            scope_id=scope_id,
            enabled=enabled,
        )
        s.add(row)
        s.commit()
        return row.id
    finally:
        s.close()


def _install_stream_spy(monkeypatch, chunks: list[str]):
    """Replace OpenAIAdapter.stream_chat with a capturing async generator."""
    captured: dict = {
        "messages": None,
        "resolved_model": None,
        "called": False,
    }

    async def _spy(
        self,
        payload,
        messages,
        *,
        resolved_model: str | None = None,
        api_key: str | None = None,
    ) -> AsyncIterator[ProviderStreamChunk]:
        captured["messages"] = [m.content for m in messages]
        captured["resolved_model"] = resolved_model
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


def _parse_sse(body: bytes | str) -> list[dict]:
    """Parse an SSE body into a list of JSON payload dicts."""
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


def _chat_stream(content: str, model: str = "gpt-4o-mini"):
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


def _log_for(request_id: str) -> RequestLogORM | None:
    Session = sessionmaker(bind=_integration_engine)
    s = Session()
    try:
        return s.get(RequestLogORM, request_id)
    finally:
        s.close()


def _request_id_from_response(resp) -> str:
    events = _parse_sse(resp.content)
    assert events, "no SSE events in response"
    return events[0]["id"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStreamingKeywordRuleApplied:
    def test_streaming_keyword_rule_applied(self, monkeypatch):
        _insert_keyword_rule(
            name="hola-rule",
            keywords=["hola"],
            model_override="gpt-4o",
            scope="global",
            priority=50,
        )
        captured = _install_stream_spy(monkeypatch, chunks=["hi!"])

        resp = _chat_stream("hola, how are you?", model="gpt-4o-mini")

        assert resp.status_code == 200
        assert captured["called"] is True
        # Router chain should have rewritten payload.model → "gpt-4o"
        # before select_route fired, so the adapter is asked for gpt-4o.
        assert captured["resolved_model"] == "gpt-4o"

        row = _log_for(_request_id_from_response(resp))
        assert row is not None
        assert row.router_used == "keyword"
        assert row.model == "gpt-4o"


class TestStreamingSemanticRouterApplied:
    def test_streaming_semantic_router_applied(self, monkeypatch):
        # The orchestrator is constructed with SemanticRouterDisabled at
        # test time (SEMANTIC_ROUTER_ENABLED defaults to False). Patching
        # its class method gives every instance the same high-confidence
        # hint without touching settings.
        monkeypatch.setattr(
            SemanticRouterDisabled,
            "get_model_hint",
            lambda self, messages, allowed_providers=None: (
                "gpt-4o",
                "coding",
                0.9,
            ),
        )
        captured = _install_stream_spy(monkeypatch, chunks=["ok"])

        resp = _chat_stream(
            "write a python function to sort a list", model="gpt-4o-mini"
        )

        assert resp.status_code == 200
        assert captured["called"] is True
        assert captured["resolved_model"] == "gpt-4o"

        row = _log_for(_request_id_from_response(resp))
        assert row is not None
        assert row.router_used == "semantic"
        assert row.model == "gpt-4o"


class TestStreamingServiceFallback:
    def test_streaming_service_fallback(self, monkeypatch):
        # No keyword rules inserted, semantic stays disabled (returns
        # None, "general", 0.0). Router chain should fall through to
        # "service".
        captured = _install_stream_spy(monkeypatch, chunks=["hello"])

        resp = _chat_stream(
            "tell me a story about a dragon", model="gpt-4o-mini"
        )

        assert resp.status_code == 200
        assert captured["called"] is True

        row = _log_for(_request_id_from_response(resp))
        assert row is not None
        assert row.router_used == "service"


class TestStreamingRouterChainRunsOnMaskedText:
    def test_streaming_router_chain_runs_on_masked_text(self, monkeypatch):
        _insert_keyword_rule(
            name="hola-rule-masked",
            keywords=["hola"],
            model_override="gpt-4o",
            scope="global",
            priority=50,
        )
        captured = _install_stream_spy(monkeypatch, chunks=["ok"])

        resp = _chat_stream(
            "my email is test@secret.com, hola!", model="gpt-4o-mini"
        )

        assert resp.status_code == 200
        assert captured["called"] is True

        # The adapter must have received the DLP-masked version of the
        # prompt — the raw email must not be present, the indexed token
        # must be. This is the primary assertion: if the router chain
        # had run BEFORE DLP, the semantic embedding and keyword text
        # would have seen the raw email.
        user_msgs = [m for m in captured["messages"] if "[EMAIL_0]" in m or "test@secret.com" in m]
        assert len(user_msgs) >= 1
        sent_to_provider = user_msgs[0]
        assert "test@secret.com" not in sent_to_provider
        assert "[EMAIL_0]" in sent_to_provider
        # And the keyword rule still fired on the surviving "hola".
        assert captured["resolved_model"] == "gpt-4o"

        row = _log_for(_request_id_from_response(resp))
        assert row is not None
        assert row.router_used == "keyword"


class TestStreamingRoutingStatsUpdated:
    def test_streaming_routing_stats_updated(self, monkeypatch):
        _insert_keyword_rule(
            name="stats-keyword",
            keywords=["stats-trigger-xyz"],
            model_override="gpt-4o",
            scope="global",
            priority=50,
        )
        _install_stream_spy(monkeypatch, chunks=["ok"])

        # Snapshot current keyword count so this test is robust against
        # leakage from earlier tests in the same process.
        before = client.get(
            "/admin/routing/stats", headers=_HEADERS
        ).json()
        before_keyword = next(
            (r["count"] for r in before["routers"] if r["router"] == "keyword"),
            0,
        )

        # Fire two streaming requests that should hit the keyword rule.
        for _ in range(2):
            resp = _chat_stream(
                "please stats-trigger-xyz this", model="gpt-4o-mini"
            )
            assert resp.status_code == 200

        after = client.get(
            "/admin/routing/stats", headers=_HEADERS
        ).json()
        after_keyword = next(
            (r["count"] for r in after["routers"] if r["router"] == "keyword"),
            0,
        )
        assert after_keyword - before_keyword == 2
