"""FIX129 A4 — baseline alert rules and their metrics.

``alert_rules`` was empty through the 34-minute outage. Now three rules are
seeded at startup (idempotently) and the evaluator can answer them:
no successful turn (with attempts), error rate with a minimum sample, and a
stuck transaction read from the pg_stat_activity sampler.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.db import Base
from app.models.alert_rule import AlertRuleORM
from app.models.request_log import RequestLogORM
from app.services import pg_activity
from app.services.alert_defaults import DEFAULT_RULES, ensure_default_rules
from app.services.alert_evaluator import ERROR_RATE_MIN_SAMPLE, AlertEvaluator
from app.services.alert_service import AlertType


@pytest.fixture(autouse=True)
def _no_sample_leaks():
    yield
    pg_activity._last = None


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def _log(session, status: str, minutes_ago: float = 1.0) -> None:
    session.add(
        RequestLogORM(
            request_id=str(uuid.uuid4()),
            tenant_id="t",
            project_id="p",
            provider="openai",
            model="m",
            status=status,
            policy_action="allow" if status != "blocked" else "block",
            latency_ms=1,
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
            estimated_cost_usd=0.0,
            created_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
        )
    )
    session.commit()


def _rule(metric: str, threshold: float, window: int = 5) -> AlertRuleORM:
    return AlertRuleORM(
        id=str(uuid.uuid4()), name=metric, metric=metric, threshold=threshold,
        window_minutes=window, scope="global", enabled=True,
    )


class TestDefaultRules:
    def test_seeds_three_rules_once(self, session):
        assert ensure_default_rules(session) == [r["name"] for r in DEFAULT_RULES]
        session.commit()
        assert ensure_default_rules(session) == []
        metrics = set(session.execute(select(AlertRuleORM.metric)).scalars())
        assert metrics == {"no_successful_turn", "error_rate", "stuck_transaction"}

    def test_operator_rule_with_same_metric_is_kept(self, session):
        mine = _rule("error_rate", 42.0)
        mine.enabled = False
        session.add(mine)
        session.commit()
        added = ensure_default_rules(session)
        assert "error_rate" not in " ".join(added).lower().replace(" ", "_") or len(added) == 2
        rows = session.execute(select(AlertRuleORM).where(AlertRuleORM.metric == "error_rate")).scalars().all()
        assert len(rows) == 1 and rows[0].threshold == 42.0 and rows[0].enabled is False


class TestMetrics:
    def test_no_successful_turn_needs_attempts(self, session):
        rule = _rule("no_successful_turn", 1.0)
        now = datetime.now(timezone.utc)
        assert AlertEvaluator._compute_metric(session, rule, now) == 0.0  # quiet ≠ outage
        _log(session, "provider_error")
        _log(session, "provider_error")
        assert AlertEvaluator._compute_metric(session, rule, now) == 2.0
        _log(session, "success")
        assert AlertEvaluator._compute_metric(session, rule, now) == 0.0

    def test_error_rate_has_a_minimum_sample_and_ignores_blocks(self, session):
        rule = _rule("error_rate", 5.0)
        now = datetime.now(timezone.utc)
        for _ in range(ERROR_RATE_MIN_SAMPLE - 1):
            _log(session, "provider_error")
        assert AlertEvaluator._compute_metric(session, rule, now) == 0.0
        _log(session, "blocked")
        # 10 rows: 9 errors, 1 block (not an error) → 90 %
        assert AlertEvaluator._compute_metric(session, rule, now) == 90.0

    def test_stuck_transaction_reads_the_sampler(self, session):
        rule = _rule("stuck_transaction", 60.0, window=1)
        now = datetime.now(timezone.utc)
        pg_activity._set_last(pg_activity.ActivitySample(at=time.time()))
        assert AlertEvaluator._compute_metric(session, rule, now) == 0.0
        pg_activity._set_last(
            pg_activity.ActivitySample(
                at=time.time(), idle_in_transaction=1, idle_in_transaction_max_age_s=95.0,
                worst=[{"pid": 1, "state": "idle in transaction", "application_name": "sran-backend-async",
                        "wait": "", "xact_age_s": 95.0, "query": "SELECT ... FOR UPDATE"}],
            )
        )
        assert AlertEvaluator._compute_metric(session, rule, now) == 95.0
        alert = AlertEvaluator._build_alert(rule, 95.0)
        assert alert.alert_type is AlertType.STUCK_TRANSACTION
        # FIX130 E4: the state, age, limit, application and pid — never the query.
        assert "has been idle for 95 s (limit 60 s) — app sran-backend-async, pid 1" in alert.message
        assert alert.details["sessions"][0]["pid"] == 1
        assert "query" not in alert.details["sessions"][0]

    def test_messages_read_like_a_notification(self):
        a = AlertEvaluator._build_alert(_rule("no_successful_turn", 1.0), 7.0)
        assert a.message.endswith("7 request(s) in the last 5 min, none succeeded")
        b = AlertEvaluator._build_alert(_rule("error_rate", 5.0), 12.5)
        assert "12.5 % of requests failed" in b.message

    @pytest.mark.asyncio
    async def test_sweep_fires_and_delivers_on_channels(self, session, monkeypatch):
        from app.services import alert_channels
        from app.core.config import settings

        sent: list[alert_channels.Notification] = []
        monkeypatch.setattr(settings, "alert_ntfy_url", "https://ntfy.example/t")
        monkeypatch.setattr(alert_channels, "deliver", lambda n: (sent.append(n) or {"ntfy": True}))
        session.add(_rule("no_successful_turn", 1.0))
        session.commit()
        # FIX131: pages with a meaningful number of attempts (3) — one failed
        # request in a quiet window is logged, not paged (test_fix131_pager).
        for _ in range(3):
            _log(session, "provider_error")
        fired = await AlertEvaluator().evaluate_all(session)
        assert len(fired) == 1
        assert len(sent) == 1 and sent[0].severity == "critical"
        assert "none completed" in sent[0].message
