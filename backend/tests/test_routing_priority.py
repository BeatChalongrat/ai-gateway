"""Routing priority chain tests: semantic > keyword > service.

These tests drive a real HTTP request through the FastAPI TestClient
and observe which model the orchestrator settled on via the response
``model`` field (the conftest stub provider echoes ``route.model_name``).

For each test we monkeypatch:
- ``GatewayOrchestrator.semantic_router`` with a fake that returns
  the desired ``(model, intent, confidence)`` triple,
- ``KeywordRouter.get_model_hint`` (class-level) when the keyword
  layer should/shouldn't fire.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.models.request_log import RequestLogORM
from app.services.keyword_router import KeywordRouter
from app.services.orchestrator import GatewayOrchestrator
from app.services.semantic_router import SemanticRouterDisabled
from tests.conftest import _integration_engine


client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"Authorization": "Bearer demo-key"}


class _FakeSemanticRouter:
    """Hands back a canned get_model_hint() return value."""

    def __init__(self, result):
        self._result = result

    def classify(self, messages):
        return (self._result[1], self._result[2])

    def get_model_hint(self, messages, allowed_providers=None):
        return self._result


def _chat(content: str = "hello there") -> dict:
    resp = client.post(
        "/v1/chat/completions",
        headers=_HEADERS,
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": content}],
            "metadata": {},
        },
    )
    return {"status": resp.status_code, "body": resp.json()}


def _log_for(request_id: str) -> RequestLogORM | None:
    Session = sessionmaker(bind=_integration_engine)
    s = Session()
    try:
        return s.get(RequestLogORM, request_id)
    finally:
        s.close()


def _install_fake_semantic(monkeypatch, result):
    """Rewire GatewayOrchestrator.__init__ so every instance created
    while the patch is active uses a FakeSemanticRouter with the
    given canned result."""
    original_init = GatewayOrchestrator.__init__

    def patched(self):
        original_init(self)
        self.semantic_router = _FakeSemanticRouter(result)

    monkeypatch.setattr(GatewayOrchestrator, "__init__", patched)


def _install_keyword_stub(monkeypatch, return_value):
    def stub(self, messages, requested_model, allowed_providers=None):
        return return_value

    monkeypatch.setattr(KeywordRouter, "get_model_hint", stub)


# ---------------------------------------------------------------------------
# Priority scenarios
# ---------------------------------------------------------------------------


class TestRoutingPriority:
    def test_semantic_wins_over_keyword_when_confident(self, monkeypatch):
        _install_fake_semantic(monkeypatch, ("gpt-4o", "coding", 0.85))
        # Keyword would otherwise force gpt-4o-mini — the semantic hint
        # must pre-empt it so this never runs, but we stub it anyway
        # to guarantee the test fails loudly if routing inverts.
        _install_keyword_stub(monkeypatch, ("gpt-4o-mini", "simple_tasks"))

        r = _chat("write a python function")
        assert r["status"] == 200
        assert r["body"]["model"] == "gpt-4o"

        log = _log_for(r["body"]["id"])
        assert log is not None
        assert log.router_used == "semantic"

    def test_keyword_wins_when_semantic_not_confident(self, monkeypatch):
        _install_fake_semantic(monkeypatch, (None, "general", 0.45))
        _install_keyword_stub(monkeypatch, ("gpt-4o", "code_tasks"))

        r = _chat("debug this code")
        assert r["status"] == 200
        assert r["body"]["model"] == "gpt-4o"

        log = _log_for(r["body"]["id"])
        assert log is not None
        assert log.router_used == "keyword"

    def test_service_wins_when_both_routers_defer(self, monkeypatch):
        _install_fake_semantic(monkeypatch, (None, "general", 0.30))
        # Keyword defers by returning (requested_model, None).
        _install_keyword_stub(monkeypatch, ("auto", None))

        r = _chat("what is the weather")
        assert r["status"] == 200
        # RoutingService default for auto → gpt-4o-mini.
        assert r["body"]["model"] == "gpt-4o-mini"

        log = _log_for(r["body"]["id"])
        assert log is not None
        assert log.router_used == "service"
