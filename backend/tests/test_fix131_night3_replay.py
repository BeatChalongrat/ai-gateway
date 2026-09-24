"""FIX131 D — NIGHT3 replayed through the senders' public entry points.

Only entry points that exist before and after FIX131 are used — the
backend's ``AlertEvaluator.evaluate_all`` and the ops watcher's
``tick(AlertState(cooldown_s=600), cfg)`` — with the I/O faked, and every
notification either sender hands to ``deliver`` is counted. So this file
runs unchanged against the d4c4325 code (RED: the pages NIGHT3 counted) and
against FIX131 (GREEN).
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.config import settings
from app.core.db import Base
from app.models.alert_rule import AlertRuleORM
from app.models.request_log import RequestLogORM
from app.ops import watch
from app.services import alert_channels, pg_activity
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
        pg_activity._last = None


@pytest.fixture()
def pages(monkeypatch):
    """Every notification either sender hands to the channels."""
    out: list = []
    monkeypatch.setattr(settings, "alert_ntfy_url", "https://ntfy.example/owner")
    monkeypatch.setattr(alert_channels, "deliver", lambda n: out.append(n) or {"ntfy": True})
    monkeypatch.setattr(watch, "deliver", lambda n: out.append(n) or {"ntfy": True})
    return out


def _quiet_io(monkeypatch, *, ready=(200, {}, None), pg_rows=(), turns=(5, 0)):
    monkeypatch.setattr(watch, "docker_containers", lambda *a, **k: [])
    monkeypatch.setattr(watch, "probe_ready", lambda url: ready)
    monkeypatch.setattr(watch, "disk_usage", lambda paths: {})
    monkeypatch.setattr(watch, "meminfo", lambda: (0, 0))
    monkeypatch.setattr(watch, "pg_activity_rows", lambda dsn: list(pg_rows))
    monkeypatch.setattr(watch, "turn_counts", lambda dsn, w: turns)


CFG = {
    "project": "ai-gateway", "sock": "/nonexistent", "backend_url": "http://backend:8080",
    "disk_paths": [], "disk_pct": 90.0, "mem_pct": 90.0, "ignore_exited": (),
    "pg_dsns": [("gateway", "postgresql://u@gw/db")], "pg_stuck_s": 60.0,
    "turn_window_min": 5, "turn_min_sample": 10, "turn_error_pct": 5.0,
}


def _rule(session, metric: str, threshold: float, window: int) -> None:
    session.add(AlertRuleORM(id=str(uuid.uuid4()), name=metric, metric=metric, threshold=threshold,
                             window_minutes=window, scope="global", enabled=True))
    session.commit()


@pytest.mark.asyncio
async def test_one_stuck_transaction_is_one_page(session, pages, monkeypatch):
    """NIGHT3: rule "Transaction stuck longer than 60 s" (p4) + ops-watch
    "backend is not ready: /ready answered 503: stuck_transaction" (p5) +
    "transaction stuck 80 s on gateway" (p5) — for ONE transaction."""
    _rule(session, "stuck_transaction", 60.0, 1)
    pg_activity._set_last(pg_activity.ActivitySample(
        at=time.time(), idle_in_transaction=1, idle_in_transaction_max_age_s=80.0,
        worst=[{"pid": 31926, "state": "idle in transaction", "application_name": "sran-backend-async",
                "wait": "Client", "xact_age_s": 80.0, "query": "SELECT … FOR UPDATE"}],
    ))
    await AlertEvaluator().evaluate_all(session)
    _quiet_io(monkeypatch, ready=(503, {"failures": ["stuck_transaction"]}, None),
              pg_rows=[{"pid": 31926, "state": "idle in transaction", "application_name": "sran-backend-async",
                        "wait_event_type": "Client", "xact_age_s": 80.0, "query": "SELECT … FOR UPDATE"}])
    watch.tick(watch.AlertState(cooldown_s=600), CFG)
    assert len(pages) == 1, [p.title for p in pages]
    assert pages[0].severity != "critical"             # degraded, not "users cannot chat"


def _failed_request(session) -> None:
    session.add(RequestLogORM(
        request_id=str(uuid.uuid4()), tenant_id="t", project_id="p", provider="openai", model="m",
        status="provider_error", policy_action="allow", latency_ms=1, prompt_tokens=1, completion_tokens=1,
        total_tokens=2, estimated_cost_usd=0.0, created_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    ))
    session.commit()


@pytest.mark.asyncio
async def test_one_failed_turn_in_a_quiet_window_is_not_a_page(session, pages, monkeypatch):
    """NIGHT3: "No successful turn in 5 min … 1 request(s)" (p4, the rule)
    AND its ops-watch twin (p5) for one failed turn."""
    _rule(session, "no_successful_turn", 1.0, 5)
    _failed_request(session)
    await AlertEvaluator().evaluate_all(session)
    _quiet_io(monkeypatch, turns=(0, 1))
    watch.tick(watch.AlertState(cooldown_s=600), {**CFG, "pg_dsns": [("chat", "postgresql://u@chat/db")]})
    assert pages == [], [p.title for p in pages]


def test_a_backend_restart_that_heals_pages_nobody(pages, monkeypatch):
    """NIGHT3: a planned backend reload paged p5 ("backend does not answer
    /ready") — one tick of a restart, then healthy again."""
    state = watch.AlertState(cooldown_s=600)
    _quiet_io(monkeypatch, ready=(None, None, "ConnectError: [Errno 111] Connection refused"))
    watch.tick(state, CFG)
    _quiet_io(monkeypatch)
    watch.tick(state, CFG)
    assert pages == [], [p.title for p in pages]


def test_a_test_stack_alert_never_reaches_the_owners_topic(monkeypatch):
    """NIGHT3 / FIX130: a test-stack page looked like a real one, and a test
    lane put 17 fake alerts on the owner's topic."""
    calls: list[httpx.Request] = []
    real_client = httpx.Client

    def _client(**kw):
        kw["transport"] = httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200, json={}))
        return real_client(**kw)

    monkeypatch.setattr(alert_channels.httpx, "Client", _client)
    monkeypatch.setattr(settings, "alert_ntfy_url", "https://ntfy.example/owner-topic")
    # Set without pydantic's field check, so the same line runs on code that
    # predates these settings (there it is simply ignored → RED).
    saved = {k: settings.__dict__.get(k, None) for k in ("alert_environment", "alert_test_ntfy_url")}
    object.__setattr__(settings, "alert_environment", "test")
    object.__setattr__(settings, "alert_test_ntfy_url", "https://ntfy.example/throwaway-test-topic")
    try:
        alert_channels.deliver(alert_channels.Notification(
            title="Chat is down", message="backend does not answer", severity="critical"))
    finally:
        for k, v in saved.items():
            if v is None:
                settings.__dict__.pop(k, None)
            else:
                object.__setattr__(settings, k, v)
    assert [str(r.url) for r in calls] == ["https://ntfy.example/throwaway-test-topic"]
    assert calls[0].headers["Title"].startswith("[TEST] ")
