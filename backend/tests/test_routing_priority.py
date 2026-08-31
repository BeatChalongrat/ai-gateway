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

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.models.keyword_rule import KeywordRuleORM
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

    async def get_model_hint_async(self, messages, allowed_providers=None):
        # Phase-1 B4: orchestrator calls the async variant; delegate to
        # the sync stub so the canned result still drives the test.
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


def _log_for_settled(request_id: str, tries: int = 10) -> RequestLogORM | None:
    """The request-log write is queued; give it a moment to land."""
    import time as _t

    for _ in range(tries):
        row = _log_for(request_id)
        if row is not None:
            return row
        _t.sleep(0.2)
    return None


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
    # FIX35 Part C — the semantic → built-in keyword → service chain no
    # longer routes ``auto``. Every bare ``auto`` goes through the four-step
    # Auto router (classify → tenant ranking → tier → ordered list). The
    # semantic router and the BUILT-IN keyword rules (the ones that once
    # downgraded "What is 15*37?" to gpt-4o-mini) are not consulted; only
    # admin-configured DB keyword rules keep precedence (project, then
    # tenant). These three tests pin that a confident semantic hint or a
    # built-in keyword hit changes NOTHING about an auto turn any more.

    def test_semantic_hint_no_longer_routes_auto(self, monkeypatch):
        _install_fake_semantic(monkeypatch, ("gpt-4o", "coding", 0.85))
        _install_keyword_stub(monkeypatch, ("gpt-4o-mini", "simple_tasks"))

        # Unique text: an identical prompt from an earlier run can be served
        # from the response cache, which writes no request_logs row.
        r = _chat(f"write a python function nonce-{uuid.uuid4().hex[:8]}")
        assert r["status"] == 200
        assert r["body"]["model"] != "auto"

        log = _log_for_settled(r["body"]["id"])
        assert log is not None
        assert log.router_used == "auto"
        assert log.task_category == "code_generate"

    def test_builtin_keyword_hit_no_longer_routes_auto(self, monkeypatch):
        _install_fake_semantic(monkeypatch, (None, "general", 0.45))
        _install_keyword_stub(monkeypatch, ("gpt-4o", "code_tasks"))

        r = _chat("debug this code")
        assert r["status"] == 200
        assert r["body"]["model"] != "auto"

        log = _log_for(r["body"]["id"])
        assert log is not None
        assert log.router_used == "auto"
        assert log.task_category == "code_debug"

    def test_plain_chat_is_classified_general_and_still_routed(self, monkeypatch):
        _install_fake_semantic(monkeypatch, (None, "general", 0.30))
        _install_keyword_stub(monkeypatch, ("auto", None))

        r = _chat("what is the weather")
        assert r["status"] == 200
        assert r["body"]["model"] != "auto"

        log = _log_for(r["body"]["id"])
        assert log is not None
        assert log.router_used == "auto"
        assert log.task_category in {"short_factual_qa", "general_chat"}


# ---------------------------------------------------------------------------
# Project-scoped keyword rules outrank semantic
# ---------------------------------------------------------------------------


def _insert_keyword_rule(
    *,
    name: str,
    keywords: list[str],
    model_override: str,
    scope: str,
    scope_id: str | None,
    priority: int = 1,
) -> str:
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
            enabled=True,
        )
        s.add(row)
        s.commit()
        return row.id
    finally:
        s.close()


@pytest.fixture
def _cleanup_keyword_rules():
    yield
    Session = sessionmaker(bind=_integration_engine)
    s = Session()
    try:
        s.query(KeywordRuleORM).delete()
        s.commit()
    finally:
        s.close()


class TestProjectScopeOverridesSemantic:
    """Project-scoped DB rules must beat the semantic router even when
    the embedding classifier is highly confident on the same prompt.
    Tenant- and global-scoped rules continue to *lose* to semantic —
    only the project layer is privileged."""

    def test_project_keyword_beats_confident_semantic(
        self, monkeypatch, _cleanup_keyword_rules
    ):
        # Semantic would otherwise force gpt-4o for "analyze" coding/
        # analysis intent — the project rule must pre-empt it.
        _install_fake_semantic(monkeypatch, ("gpt-4o", "analysis", 0.95))
        _insert_keyword_rule(
            name="project_route_analyze",
            keywords=["analyze"],
            model_override="qwen-plus",
            scope="project",
            scope_id="test-project",
        )

        r = _chat("please analyze this dataset")
        assert r["status"] == 200
        assert r["body"]["model"] == "qwen-plus"

        log = _log_for(r["body"]["id"])
        assert log is not None
        assert log.router_used == "keyword_project"

    def test_tenant_keyword_rule_is_admin_intent_and_outranks_the_auto_router(
        self, monkeypatch, _cleanup_keyword_rules
    ):
        # FIX35 Part C — semantic routing is retired for ``auto``; an
        # admin-configured TENANT rule is explicit intent and wins over the
        # Auto router (project rules still sit above tenant rules).
        _install_fake_semantic(monkeypatch, ("gpt-4o", "analysis", 0.95))
        _insert_keyword_rule(
            name="tenant_route_analyze",
            keywords=["analyze"],
            model_override="qwen-plus",
            scope="tenant",
            scope_id="test-tenant",
        )

        r = _chat("please analyze this dataset")
        assert r["status"] == 200
        assert r["body"]["model"] == "qwen-plus"

        log = _log_for(r["body"]["id"])
        assert log is not None
        assert log.router_used == "keyword_tenant"
