"""FIX130 E3 — a chat turn that fails before any provider is called writes
exactly ONE ``request_logs`` row, and the FIX129 alert rules count it.

FIX129 measured the gap: 12 dead-model turns, 0 rows — the error went to
``handle_gateway_error``, which only logs, so ``error_rate`` and
``no_successful_turn`` (both read ``request_logs``) could not see a dead
model. These tests drive the REAL ``/v1/chat/completions`` route (stream
and non-stream) and count rows in the test database; the admission /
policy / provider-reached cases pin the rows that must NOT appear
(pre_provider_log module doc).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.db import Base
from app.core.exceptions import (
    ContextLengthExceededError,
    ModelNotServed,
    NoCapableModelError,
    RateLimitExceededError,
)
from app.main import app
from app.models.alert_rule import AlertRuleORM
from app.models.request_log import RequestLogORM
from app.services.alert_evaluator import ERROR_RATE_MIN_SAMPLE, AlertEvaluator
from app.services.orchestrator import GatewayOrchestrator
from app.services.provider_key_service import ProviderKeyService
from app.services.request_log_service import RequestLogService, _build_request_log_orm
from tests.conftest import _IntegrationSession

client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"Authorization": "Bearer demo-key"}
_PRE = "pre_provider_error"


def _post(stream: bool):
    # A fresh prompt per call: never the response cache.
    return client.post("/v1/chat/completions", headers=_HEADERS, json={
        "model": "gpt-4o-mini", "stream": stream, "metadata": {},
        "messages": [{"role": "user", "content": f"fix130 e3 turn {uuid.uuid4().hex[:10]}"}],
    })


def _row_ids(**filters) -> set[str]:
    with _IntegrationSession() as s:
        q = s.query(RequestLogORM.request_id)
        for col, value in filters.items():
            q = q.filter(getattr(RequestLogORM, col) == value)
        return {r[0] for r in q.all()}


def _row(request_id: str) -> RequestLogORM:
    with _IntegrationSession() as s:
        return s.query(RequestLogORM).filter(RequestLogORM.request_id == request_id).one()


@pytest.fixture
def dead_model(monkeypatch):
    """No usable key reaches the box that serves the model — FIX129's dead model."""
    async def _no_key(self, session, provider_name, model=None):
        return None

    async def _diagnose(self, session, provider_name, model_name):
        return ModelNotServed(
            "No configured key currently reaches the endpoint that serves this model.",
            details={"provider": provider_name, "model": model_name},
        )

    monkeypatch.setattr(ProviderKeyService, "get_api_key_async", _no_key)
    monkeypatch.setattr(ProviderKeyService, "diagnose_missing_key_async", _diagnose)


def _assert_one_new_pre_provider_row(before_all: set[str], code: str) -> RequestLogORM:
    new = _row_ids() - before_all
    assert len(new) == 1, f"expected exactly one new request_logs row, got {len(new)}"
    row = _row(new.pop())
    assert row.status == _PRE
    assert row.error_code == code
    assert row.provider == ""
    assert (row.prompt_tokens, row.completion_tokens, row.total_tokens) == (0, 0, 0)
    assert row.estimated_cost_usd == 0.0
    assert row.tenant_id == "test-tenant" and row.project_id == "test-project"
    assert row.model == "gpt-4o-mini"
    return row


# ---------------------------------------------------------------------------
# The rows that must appear
# ---------------------------------------------------------------------------

def test_stream_dead_model_writes_exactly_one_pre_provider_row(dead_model):
    before = _row_ids()
    resp = _post(stream=True)
    assert resp.status_code == 502, resp.text
    assert resp.json()["error"]["code"] == "model_not_served"
    _assert_one_new_pre_provider_row(before, "model_not_served")


def test_non_stream_dead_model_writes_exactly_one_pre_provider_row(dead_model):
    before = _row_ids()
    resp = _post(stream=False)
    assert resp.status_code == 502, resp.text
    assert resp.json()["error"]["code"] == "model_not_served"
    _assert_one_new_pre_provider_row(before, "model_not_served")


def test_stream_no_capable_model_writes_exactly_one_pre_provider_row(monkeypatch):
    async def _router(self, ctx, payload, dict_messages, session, log):
        raise NoCapableModelError(details={"missing": ["tools"]})

    monkeypatch.setattr(GatewayOrchestrator, "_apply_router_chain", _router)
    before = _row_ids()
    resp = _post(stream=True)
    assert resp.status_code == 400, resp.text
    _assert_one_new_pre_provider_row(before, "no_capable_model")


# ---------------------------------------------------------------------------
# The rows that must NOT appear (or must stay one)
# ---------------------------------------------------------------------------

def test_dlp_blocked_turn_still_writes_exactly_one_row(monkeypatch):
    """The block path writes its own ``blocked`` row before raising; the
    wrapper must not add a second one."""
    from app.domain.dlp import (
        DetectionMatch,
        DetectionResult,
        EnforcementAction,
        EnforcementDecision,
        MatchAction,
        SensitivityLevel,
    )

    match = DetectionMatch(
        detector_name="api_key", entity_type="API_KEY", category="credential",
        start=0, end=4, matched_text="sk-x", severity=SensitivityLevel.CRITICAL,
    )

    async def _scan(self, registry, messages, *, direction, scan_system_messages):
        return DetectionResult(matches=[match])

    def _evaluate(self, detection_result, profile, **kw):
        return EnforcementDecision(match_actions=[MatchAction(
            match=match, action=EnforcementAction.BLOCK,
            reason_code="credential", reason_message="blocked",
        )])

    monkeypatch.setattr(GatewayOrchestrator, "_scan_messages_async", _scan)
    monkeypatch.setattr(GatewayOrchestrator, "_scan_and_evaluate", _evaluate)
    before = _row_ids()
    resp = _post(stream=True)
    assert resp.status_code == 403, resp.text
    new = _row_ids() - before
    assert len(new) == 1
    row = _row(new.pop())
    assert (row.status, row.error_code, row.policy_action) == ("blocked", "dlp_violation", "block")


def test_a_turn_that_already_wrote_its_row_gets_no_second_one(monkeypatch):
    from app.domain.observability import RequestRecord, RequestStatus

    async def _router(self, ctx, payload, dict_messages, session, log):
        await self.request_log_service.persist_async(session, RequestRecord(
            request_id=ctx.request_id, tenant_id=ctx.tenant_id, project_id=ctx.project_id,
            status=RequestStatus.BLOCKED, policy_action="block", error_code="guardrail_blocked",
        ))
        await session.commit()
        raise NoCapableModelError()

    monkeypatch.setattr(GatewayOrchestrator, "_apply_router_chain", _router)
    before = _row_ids()
    assert _post(stream=True).status_code == 400
    new = _row_ids() - before
    assert len(new) == 1 and _row(new.pop()).status == "blocked"


def test_rate_limited_turn_writes_no_row(monkeypatch):
    """An admission refusal is the system working, not a failed turn: no row,
    so one throttled client cannot trip error_rate."""
    async def _refuse(self, auth, ctx, session):
        raise RateLimitExceededError()

    monkeypatch.setattr(GatewayOrchestrator, "_run_preflight_checks", _refuse)
    before = _row_ids()
    for stream in (True, False):
        assert _post(stream=stream).status_code == 429
    assert _row_ids() == before


def test_a_failure_after_the_provider_was_called_is_not_marked_pre_provider(monkeypatch):
    from app.services.provider_dispatcher import ProviderDispatcher

    async def _dispatch(self, **kw):
        raise ContextLengthExceededError(details={"provider": "openai"})

    monkeypatch.setattr(ProviderDispatcher, "dispatch_with_fallback", _dispatch)
    before = _row_ids(status=_PRE)
    assert _post(stream=False).status_code == 400
    assert _row_ids(status=_PRE) == before


# ---------------------------------------------------------------------------
# The FIX129 alert rules see a dead model
# ---------------------------------------------------------------------------

def test_dead_model_turns_trip_no_successful_turn_and_error_rate(dead_model, monkeypatch):
    """Every row the route writes for ERROR_RATE_MIN_SAMPLE dead-model turns,
    replayed into an empty database, is what the evaluator reads."""
    written = []
    original = RequestLogService.persist_async

    async def _spy(self, session, record):
        written.append(record)
        await original(self, session, record)

    monkeypatch.setattr(RequestLogService, "persist_async", _spy)
    for i in range(ERROR_RATE_MIN_SAMPLE):
        assert _post(stream=bool(i % 2)).status_code == 502

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        for record in written:
            s.add(_build_request_log_orm(record))
        s.commit()
        now = datetime.now(timezone.utc)

        def rule(metric, threshold):
            return AlertRuleORM(id=str(uuid.uuid4()), name=metric, metric=metric, threshold=threshold,
                                window_minutes=5, scope="global", enabled=True)

        assert AlertEvaluator._compute_metric(s, rule("no_successful_turn", 1.0), now) == float(ERROR_RATE_MIN_SAMPLE)
        assert AlertEvaluator._compute_metric(s, rule("error_rate", 5.0), now) == 100.0
        # No provider failed: the provider_failure rule stays quiet.
        assert AlertEvaluator._compute_metric(s, rule("provider_failure", 1.0), now) == 0.0
    finally:
        s.close()
        engine.dispose()
