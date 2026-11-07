"""FIX129 A2/A3 — /ready goes red on a stuck transaction; the sampler logs it.

During the 2026-09-22 wedge every engine check stayed green while one
transaction sat idle in transaction holding a row lock. /ready now reads
the pg_stat_activity sampler's last sample and reports the last turn's age.
"""

from __future__ import annotations

import time

import pytest
import structlog
from fastapi.testclient import TestClient

from app.core import turn_pulse
from app.main import app
from app.services import pg_activity
from app.services.pg_activity import ActivitySample


@pytest.fixture(autouse=True)
def _no_sample_leaks():
    """The sampler state is process-wide; leave it as we found it (None)."""
    yield
    pg_activity._last = None
    turn_pulse.reset()


def test_a_stale_sample_does_not_hold_ready_red():
    client = TestClient(app)
    old = _stuck(120.0)
    old.at = time.time() - 3600
    pg_activity._set_last(old)
    body = client.get("/ready").json()
    assert "stuck_transaction" not in body["failures"]
    assert body["checks"]["pg_activity"]["stuck_age_s"] == 120.0


def _stuck(age: float) -> ActivitySample:
    return ActivitySample(
        at=time.time(), idle_in_transaction=1, idle_in_transaction_max_age_s=age,
        lock_waiters=1, lock_wait_max_age_s=age - 1,
        worst=[{"pid": 31926, "state": "idle in transaction", "application_name": "sran-backend-async",
                "wait": "Client", "xact_age_s": age, "query": "SELECT portal_users ... FOR UPDATE"}],
    )


def test_ready_is_red_on_a_stuck_transaction():
    client = TestClient(app)
    pg_activity._set_last(_stuck(120.0))
    r = client.get("/ready")
    body = r.json()
    assert r.status_code == 503
    assert "stuck_transaction" in body["failures"]
    assert body["checks"]["pg_activity"]["stuck_age_s"] == 120.0
    assert body["checks"]["pg_activity"]["idle_in_transaction"] == 1

    pg_activity._set_last(ActivitySample(at=time.time()))
    body = client.get("/ready").json()
    assert "stuck_transaction" not in body["failures"]
    assert body["checks"]["pg_activity"]["stuck_age_s"] == 0.0


def test_ready_reports_the_last_turn():
    client = TestClient(app)
    pg_activity._set_last(ActivitySample(at=time.time()))
    turn_pulse.reset()
    body = client.get("/ready").json()
    assert body["checks"]["turns"] == {"last_success_age_s": None, "last_attempt_age_s": None}
    turn_pulse.mark("provider_error")
    turn_pulse.mark("success")
    body = client.get("/ready").json()
    assert body["checks"]["turns"]["last_success_age_s"] is not None
    assert body["checks"]["turns"]["last_success_age_s"] < 5


def test_record_sample_logs_the_sessions_when_stuck():
    from app.observability.metrics import PG_STUCK_TRANSACTION_AGE_SECONDS

    with structlog.testing.capture_logs() as logs:
        assert pg_activity.record_sample(_stuck(75.0), stuck_after_s=60.0) is True
        assert pg_activity.record_sample(_stuck(10.0), stuck_after_s=60.0) is False
    warn = [l for l in logs if l.get("event") == "pg_stuck_transaction"]
    assert len(warn) == 1
    assert warn[0]["sessions"][0]["pid"] == 31926
    assert warn[0]["stuck_age_s"] == 75.0
    assert PG_STUCK_TRANSACTION_AGE_SECONDS._value.get() == 10.0
    assert pg_activity.stuck_age_seconds() == 10.0


def test_sample_once_is_empty_off_postgres():
    from tests.conftest import _integration_engine
    from sqlalchemy.orm import sessionmaker

    s = sessionmaker(bind=_integration_engine)()
    try:
        sample = pg_activity.sample_once(s)
    finally:
        s.close()
    assert sample.error is None
    assert sample.stuck_age_s == 0.0 and sample.worst == []
