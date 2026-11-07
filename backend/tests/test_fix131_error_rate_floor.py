"""FIX131 D — an error-rate page needs a minimum number of FAILED requests.

Live, 2026-09-24 02:59 UTC: the owner sent 10 requests to qwen3.8-27b-fp8 in
five minutes; one ended ``output_limit_reached`` (qwen's thinking used its
whole budget — a model outcome, docs/TESTING_MODELS.md). 1 / 10 = 10 % > 5 %, so the
"Error rate above 5 %" rule paged the owner's phone at top priority for a
single failed message. The card's rule: one failed turn is not a page.

The ratio stays the trigger; the page additionally needs at least
``alert_page_min_attempts`` (3) failed requests in the window — the same
floor the pager already uses for "no successful turn". Both senders get it:
the backend rule (request_logs) and ops-watch's turn rule (chat DB).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.db import Base
from app.models.alert_rule import AlertRuleORM
from app.models.request_log import RequestLogORM
from app.ops.watch import classify_turns
from app.services.alert_evaluator import AlertEvaluator


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def _log(session, status: str) -> None:
    row = RequestLogORM(
        request_id=str(uuid.uuid4()), tenant_id="t", project_id="p", provider="sranai", model="qwen3.8-27b-fp8",
        status=status, policy_action="allow", latency_ms=1, prompt_tokens=1, completion_tokens=1,
        total_tokens=2, estimated_cost_usd=0.0,
    )
    row.created_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    session.add(row)
    session.flush()


def _rule() -> AlertRuleORM:
    return AlertRuleORM(id=str(uuid.uuid4()), name="error_rate", metric="error_rate", threshold=5.0,
                        window_minutes=5, scope="global", enabled=True)


def test_one_failed_request_in_ten_is_not_a_page(session):
    # the live 02:59 shape: 9 answered, 1 output_limit_reached
    for _ in range(9):
        _log(session, "success")
    _log(session, "provider_error")
    assert AlertEvaluator._compute_metric(session, _rule(), datetime.now(timezone.utc)) == 0.0


def test_three_failed_requests_still_page(session):
    for _ in range(7):
        _log(session, "success")
    for _ in range(3):
        _log(session, "provider_error")
    assert AlertEvaluator._compute_metric(session, _rule(), datetime.now(timezone.utc)) == 30.0


def test_ops_watch_turn_rate_needs_three_failed_turns():
    one = classify_turns(9, 1, window_min=5, min_sample=10, max_error_pct=5.0, min_attempts=3)
    assert not [f for f in one if f.key == "turns:error_rate"]
    three = classify_turns(7, 3, window_min=5, min_sample=10, max_error_pct=5.0, min_attempts=3)
    assert [f for f in three if f.key == "turns:error_rate"]
