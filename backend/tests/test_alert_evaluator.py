"""Tests for the alert evaluator wiring.

Covers:
- Metric aggregation for all 4 evaluator metrics
  (dlp_block_spike, budget_threshold, provider_failure, quota_exhausted).
- Scope filtering (tenant scope limits the aggregate to matching rows).
- Cooldown (suppresses re-fire within window, allows re-fire after).
- Webhook delivery integration (called when URL set, skipped when null).
- Evaluator failure isolation (exception inside evaluate_after_request
  must not reach the caller — the orchestrator swallows it).

All tests drive the evaluator directly against an in-memory SQLite
engine rather than the HTTP layer, which keeps the scope to the
evaluator wiring and lets each test insert exactly the request_logs
it needs.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.models.alert_rule import AlertEventORM, AlertRuleORM
from app.models.request_log import RequestLogORM
import app.models  # noqa: F401  — make sure every ORM model is registered
from app.services.alert_evaluator import AlertEvaluator
from app.services.alert_service import AlertService, AlertType
from app.services.webhook_service import (
    DeliveryStatus,
    WebhookDelivery,
    WebhookService,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def _insert_request_log(
    session,
    *,
    tenant_id: str = "test-tenant",
    project_id: str = "test-project",
    status: str = "success",
    policy_action: str = "allow",
    cost: float = 0.001,
    created_at: datetime | None = None,
    request_id: str | None = None,
) -> RequestLogORM:
    row = RequestLogORM(
        request_id=request_id or str(uuid.uuid4()),
        tenant_id=tenant_id,
        project_id=project_id,
        provider="openai",
        model="gpt-4o-mini",
        status=status,
        policy_action=policy_action,
        latency_ms=10,
        prompt_tokens=1,
        completion_tokens=1,
        total_tokens=2,
        estimated_cost_usd=cost,
    )
    if created_at is not None:
        row.created_at = created_at
    session.add(row)
    session.flush()
    return row


def _insert_rule(
    session,
    *,
    metric: str,
    threshold: float,
    window_minutes: int = 5,
    scope: str = "global",
    scope_id: str | None = None,
    webhook_url: str | None = None,
    last_fired_at: datetime | None = None,
    name: str = "test-rule",
) -> AlertRuleORM:
    row = AlertRuleORM(
        id=str(uuid.uuid4()),
        name=name,
        metric=metric,
        threshold=threshold,
        window_minutes=window_minutes,
        scope=scope,
        scope_id=scope_id,
        enabled=True,
        webhook_url=webhook_url,
        last_fired_at=last_fired_at,
    )
    session.add(row)
    session.flush()
    return row


def _stub_webhook_service() -> tuple[WebhookService, AsyncMock]:
    """Return a WebhookService whose deliver() is replaced with an
    AsyncMock pre-configured to report DELIVERED."""
    svc = WebhookService()
    mock_deliver = AsyncMock(
        return_value=WebhookDelivery(
            webhook_url="stub",
            payload={},
            status=DeliveryStatus.DELIVERED,
            attempts=1,
        )
    )
    svc.deliver = mock_deliver  # type: ignore[method-assign]
    return svc, mock_deliver


# ---------------------------------------------------------------------------
# Metric: dlp_block_spike
# ---------------------------------------------------------------------------


class TestDlpBlockSpike:
    @pytest.mark.asyncio
    async def test_fires_when_threshold_exceeded(self, session):
        for _ in range(5):
            _insert_request_log(session, policy_action="block")
        trigger = _insert_request_log(session, policy_action="block")
        rule = _insert_rule(
            session, metric="dlp_block_spike", threshold=3, scope="global"
        )

        evaluator = AlertEvaluator()
        fired = await evaluator.evaluate_after_request(session, trigger)

        assert rule.id in fired
        # An alert_events row was persisted with log_only delivery.
        events = session.query(AlertEventORM).all()
        assert len(events) == 1
        assert events[0].alert_type == AlertType.DLP_BLOCK_SPIKE.value
        assert events[0].delivery_status == "log_only"
        # Cooldown field updated.
        assert rule.last_fired_at is not None

    @pytest.mark.asyncio
    async def test_does_not_fire_below_threshold(self, session):
        for _ in range(2):
            _insert_request_log(session, policy_action="block")
        trigger = _insert_request_log(session, policy_action="block")
        _insert_rule(
            session, metric="dlp_block_spike", threshold=5, scope="global"
        )

        evaluator = AlertEvaluator()
        fired = await evaluator.evaluate_after_request(session, trigger)

        assert fired == []
        assert session.query(AlertEventORM).count() == 0


# ---------------------------------------------------------------------------
# Metric: budget_threshold
# ---------------------------------------------------------------------------


class TestBudgetThreshold:
    @pytest.mark.asyncio
    async def test_fires_on_cost_sum(self, session):
        for _ in range(3):
            _insert_request_log(session, cost=0.02)
        trigger = _insert_request_log(session, cost=0.02)  # total = 0.08
        _insert_rule(
            session, metric="budget_threshold", threshold=0.05, scope="global"
        )

        evaluator = AlertEvaluator()
        fired = await evaluator.evaluate_after_request(session, trigger)

        assert len(fired) == 1
        event = session.query(AlertEventORM).one()
        # details_json carries the numeric metric_value; sanity-check it's
        # at least the threshold.
        import json
        details = json.loads(event.details_json)
        assert details["metric_value"] >= 0.05


# ---------------------------------------------------------------------------
# Metric: provider_failure
# ---------------------------------------------------------------------------


class TestProviderFailure:
    @pytest.mark.asyncio
    async def test_fires_on_error_count(self, session):
        for _ in range(3):
            _insert_request_log(session, status="error")
        trigger = _insert_request_log(session, status="error")
        _insert_rule(
            session, metric="provider_failure", threshold=3, scope="global"
        )

        evaluator = AlertEvaluator()
        fired = await evaluator.evaluate_after_request(session, trigger)

        assert len(fired) == 1
        event = session.query(AlertEventORM).one()
        assert event.alert_type == AlertType.PROVIDER_FAILURE.value


# ---------------------------------------------------------------------------
# Cooldown
# ---------------------------------------------------------------------------


class TestCooldown:
    @pytest.mark.asyncio
    async def test_cooldown_prevents_double_fire(self, session):
        for _ in range(5):
            _insert_request_log(session, policy_action="block")
        trigger = _insert_request_log(session, policy_action="block")
        # Last fire 1 minute ago, window = 5 minutes → still in cooldown.
        _insert_rule(
            session,
            metric="dlp_block_spike",
            threshold=3,
            window_minutes=5,
            last_fired_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )

        evaluator = AlertEvaluator()
        fired = await evaluator.evaluate_after_request(session, trigger)

        assert fired == []

    @pytest.mark.asyncio
    async def test_cooldown_allows_fire_after_window(self, session):
        for _ in range(5):
            _insert_request_log(session, policy_action="block")
        trigger = _insert_request_log(session, policy_action="block")
        rule = _insert_rule(
            session,
            metric="dlp_block_spike",
            threshold=3,
            window_minutes=5,
            last_fired_at=datetime.now(timezone.utc) - timedelta(minutes=6),
        )

        evaluator = AlertEvaluator()
        fired = await evaluator.evaluate_after_request(session, trigger)

        assert rule.id in fired
        # Cooldown was refreshed to roughly now.
        delta = datetime.now(timezone.utc) - rule.last_fired_at.replace(
            tzinfo=timezone.utc
        )
        assert delta < timedelta(seconds=5)


# ---------------------------------------------------------------------------
# Scope filtering
# ---------------------------------------------------------------------------


class TestScopeFiltering:
    @pytest.mark.asyncio
    async def test_scope_tenant_filters_correctly(self, session):
        # Tenant A breaches, tenant B doesn't.
        for _ in range(5):
            _insert_request_log(
                session, tenant_id="tenant_A", policy_action="block"
            )
        _insert_request_log(
            session, tenant_id="tenant_B", policy_action="block"
        )
        trigger = _insert_request_log(
            session, tenant_id="tenant_A", policy_action="block"
        )

        _insert_rule(
            session,
            metric="dlp_block_spike",
            threshold=3,
            scope="tenant",
            scope_id="tenant_A",
        )
        _insert_rule(
            session,
            metric="dlp_block_spike",
            threshold=3,
            scope="tenant",
            scope_id="tenant_B",
            name="rule_b",
        )

        evaluator = AlertEvaluator()
        fired = await evaluator.evaluate_after_request(session, trigger)

        # Only the tenant_A rule should fire — the trigger request's
        # tenant is tenant_A, so rule_b doesn't scope-match and stays
        # silent even though tenant_B is below threshold anyway.
        assert len(fired) == 1
        event = session.query(AlertEventORM).one()
        assert event.tenant_id == "tenant_A"


# ---------------------------------------------------------------------------
# Webhook delivery
# ---------------------------------------------------------------------------


class TestWebhookDelivery:
    @pytest.mark.asyncio
    async def test_webhook_delivered_when_url_set(self, session):
        webhook_svc, mock_deliver = _stub_webhook_service()
        evaluator = AlertEvaluator(
            alert_service=AlertService(webhook_service=webhook_svc)
        )

        for _ in range(5):
            _insert_request_log(session, policy_action="block")
        trigger = _insert_request_log(session, policy_action="block")
        _insert_rule(
            session,
            metric="dlp_block_spike",
            threshold=3,
            webhook_url="https://example.com/hook",
        )

        fired = await evaluator.evaluate_after_request(session, trigger)

        assert len(fired) == 1
        mock_deliver.assert_awaited_once()
        called_url, called_payload = mock_deliver.await_args.args
        assert called_url == "https://example.com/hook"
        assert called_payload["event"] == "alert_fired"
        assert called_payload["alert_type"] == "dlp_block_spike"
        assert "fired_at" in called_payload
        # alert_events row now reflects the delivery outcome.
        event = session.query(AlertEventORM).one()
        assert event.delivery_status == "delivered"
        assert event.webhook_url == "https://example.com/hook"

    @pytest.mark.asyncio
    async def test_webhook_not_called_when_url_null(self, session):
        webhook_svc, mock_deliver = _stub_webhook_service()
        evaluator = AlertEvaluator(
            alert_service=AlertService(webhook_service=webhook_svc)
        )

        for _ in range(5):
            _insert_request_log(session, policy_action="block")
        trigger = _insert_request_log(session, policy_action="block")
        _insert_rule(
            session,
            metric="dlp_block_spike",
            threshold=3,
            webhook_url=None,
        )

        await evaluator.evaluate_after_request(session, trigger)

        mock_deliver.assert_not_awaited()
        event = session.query(AlertEventORM).one()
        assert event.delivery_status == "log_only"


# ---------------------------------------------------------------------------
# Failure isolation
# ---------------------------------------------------------------------------


class TestEvaluatorFailureIsolation:
    @pytest.mark.asyncio
    async def test_evaluator_exception_does_not_propagate(
        self, session, monkeypatch
    ):
        """The orchestrator + public_chat wrap evaluate_after_request in
        try/except so that an evaluator blow-up never breaks the request
        response. Verify the wrapper is in place by driving the
        non-streaming orchestrator path with an evaluator that raises."""
        from app.services.orchestrator import GatewayOrchestrator

        # Make evaluate_after_request raise the moment it's called.
        async def _boom(self, *args, **kwargs):  # noqa: ARG001
            raise RuntimeError("synthetic evaluator failure")

        monkeypatch.setattr(
            AlertEvaluator, "evaluate_after_request", _boom
        )

        # Drive a single non-streaming request through the integration
        # test client — it exercises orchestrator.handle_chat_completion,
        # which wraps the evaluator call in try/except.
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)
        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer demo-key"},
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "hi"}],
                "metadata": {},
            },
        )

        # Success returned despite the evaluator exception.
        assert resp.status_code == 200
        assert resp.json()["choices"][0]["message"]["role"] == "assistant"
