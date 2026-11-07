"""FIX130 E4 — an alert says what happened and the numbers; nothing else.

Before FIX130 the stuck-transaction alert put the head of the SQL of the
oldest session in the ntfy/e-mail body (``_notification_text``), the whole
``sessions`` list with each query in ``alert_events.details_json``, in the
``alert_emitted`` log line and in the per-rule webhook payload, and the ops
watcher put ``query[:100]`` in its message. A query carries whatever the
application bound into it: message text, e-mail addresses, tokens.

This file feeds every alert path a session whose query holds SQL, a DSN with
a password, a JWT, an API key and a piece of user text, and fails if any of
them reaches anything an alert produces: the ntfy body and title, the e-mail,
the global webhook, the rule's own webhook, the ``alert_events`` row, the
``alert_emitted`` log line, or the ops watcher's notification. The SQL head
stays in ONE place: the ``pg_stuck_transaction`` application log line.

``infra/backup/notify.sh`` is covered by infra/backup/tests/test_notify.sh
(it runs in the backup image, not in this lane).
"""

from __future__ import annotations

import json
import re
import time
import uuid
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.config import settings
from app.core.db import Base
from app.models.alert_rule import AlertEventORM, AlertRuleORM
from app.services import alert_channels, pg_activity
from app.services.alert_evaluator import AlertEvaluator
from app.services.alert_service import Alert, AlertService, AlertSeverity, AlertType
from app.services.webhook_service import DeliveryStatus, WebhookDelivery, WebhookService

# --- the hostile inputs ------------------------------------------------------

USER_TEXT = "ขอเลื่อนนัดคุณสมชาย private-note-7f3a"
SECRET_DSN = "postgresql://app:hunter2pass@db.internal:5432/sran"
JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJ1LTEyMyIsImp0aSI6ImFiYyJ9"
    ".c2lnbmF0dXJlLWJ5dGVzLWhlcmUtMTIzNDU2"
)
API_KEY = "sk-live-9f8e7d6c5b4a39281706f5e4"
HOSTILE_QUERY = (
    f"UPDATE chat.messages_masked SET content = '{USER_TEXT}' "
    f"WHERE token = '{JWT}' /* {SECRET_DSN} {API_KEY} */"
)

LEAK_PATTERNS = {
    "sql": re.compile(r"\b(SELECT|UPDATE|INSERT|DELETE)\b|\bFROM\s+\w|\bset\s+content\b", re.I),
    "credential_url": re.compile(r"[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]+@", re.I),
    "jwt": re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    "api_key": re.compile(r"\bsk-[A-Za-z0-9-]{12,}"),
    "password": re.compile("hunter2pass"),
    "user_text": re.compile("private-note-7f3a|สมชาย"),
}


def leaks(*parts) -> list[str]:
    """Names of the leak patterns found anywhere in ``parts``."""
    blob = "\n".join(
        p if isinstance(p, str) else json.dumps(p, ensure_ascii=False, default=str) for p in parts
    )
    return [name for name, pat in LEAK_PATTERNS.items() if pat.search(blob)]


def test_the_detector_detects():
    """Guard the guard: the raw query trips every pattern."""
    assert leaks(HOSTILE_QUERY) == list(LEAK_PATTERNS)
    assert leaks("a database transaction has been idle for 64 s (limit 60 s) — app psql, pid 262") == []


# --- fixtures ----------------------------------------------------------------


class _Recorder:
    """Stands in for a module's structlog logger (``capture_logs`` misses
    loggers cached before another test reset the structlog config)."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def _rec(self, event, **kw):
        self.events.append({"event": event, **kw})

    info = warning = error = debug = exception = _rec


@pytest.fixture()
def logs(monkeypatch) -> list[dict]:
    from app.services import alert_service as alert_service_module

    rec = _Recorder()
    monkeypatch.setattr(alert_service_module, "logger", rec)
    monkeypatch.setattr(pg_activity, "logger", rec)
    return rec.events


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


@pytest.fixture()
def channels(monkeypatch):
    """All three global channels configured; every outbound byte captured."""
    sent: dict[str, list] = {"http": [], "email": []}

    def handler(request: httpx.Request) -> httpx.Response:
        sent["http"].append(request)
        return httpx.Response(200, json={"id": "x"})

    real_client = httpx.Client

    def _client(**kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real_client(**kw)

    def _send_email(**kw):
        sent["email"].append(kw)
        return SimpleNamespace(delivered=True)

    monkeypatch.setattr(alert_channels.httpx, "Client", _client)
    monkeypatch.setattr("app.services.email.sender.send_email", _send_email)
    monkeypatch.setattr(settings, "alert_ntfy_url", "https://ntfy.example/sran-test")
    monkeypatch.setattr(settings, "alert_ntfy_token", "")
    monkeypatch.setattr(settings, "alert_email_to", "ops@example.com")
    monkeypatch.setattr(settings, "alert_webhook_url", "https://hooks.example/global")
    return sent


def _outbound(sent: dict) -> list:
    """Everything the channels put on the wire, as text."""
    out: list = []
    for req in sent["http"]:
        out.append(str(req.url))
        out.append(dict(req.headers))
        out.append(req.content.decode("utf-8", "replace"))
    for mail in sent["email"]:
        out.append(mail)
    return out


def _stuck_sample() -> pg_activity.ActivitySample:
    return pg_activity.ActivitySample(
        at=time.time(),
        idle_in_transaction=1,
        idle_in_transaction_max_age_s=64.2,
        worst=[
            {"pid": 262, "state": "idle in transaction", "application_name": "psql",
             "wait": "Client", "xact_age_s": 64.2, "query": HOSTILE_QUERY},
            {"pid": 263, "state": "active", "application_name": "sran-backend",
             "wait": "Lock", "xact_age_s": 30.0, "query": f"SELECT * FROM portal_users /* {USER_TEXT} */"},
        ],
    )


# --- backend: the stuck-transaction rule, end to end ------------------------


@pytest.mark.asyncio
async def test_stuck_transaction_alert_is_numbers_not_sql(session, channels, logs):
    rule = AlertRuleORM(
        id=str(uuid.uuid4()), name="stuck transaction", metric="stuck_transaction",
        threshold=60.0, window_minutes=1, scope="global", enabled=True,
        webhook_url="https://hooks.example/rule",
    )
    session.add(rule)
    session.commit()
    pg_activity._set_last(_stuck_sample())

    rule_hook = WebhookService()
    rule_payloads: list[dict] = []

    async def _deliver(url, payload):
        rule_payloads.append(payload)
        return WebhookDelivery(webhook_url=url, payload=payload, status=DeliveryStatus.DELIVERED, attempts=1)

    rule_hook.deliver = _deliver  # type: ignore[method-assign]

    fired = await AlertEvaluator(alert_service=AlertService(webhook_service=rule_hook)).evaluate_all(session)

    assert fired == [rule.id]
    event = session.query(AlertEventORM).one()
    emitted = [e for e in logs if e.get("event") == "alert_emitted"]
    assert emitted, "alert_emitted was not logged"
    assert len(channels["http"]) == 2 and len(channels["email"]) == 1  # ntfy + global webhook, e-mail
    assert len(rule_payloads) == 1

    # What happened, in numbers and names a person can act on.
    assert "a database transaction has been idle for 64 s (limit 60 s)" in event.message
    assert "app psql" in event.message and "pid 262" in event.message
    ntfy = next(r for r in channels["http"] if r.url.host == "ntfy.example")
    assert "idle for 64 s" in ntfy.content.decode("utf-8")
    details = json.loads(event.details_json)
    assert details["sessions"][0]["pid"] == 262
    assert details["sessions"][0]["application_name"] == "psql"

    found = leaks(
        event.message, event.details_json or "",
        *_outbound(channels), rule_payloads,
        [{k: v for k, v in e.items()} for e in emitted],
    )
    assert found == [], f"alert output leaked: {found}"


def test_the_sql_head_stays_in_the_application_log(logs):
    """The one place the query head is kept: the pg_stuck_transaction line."""
    assert pg_activity.record_sample(_stuck_sample(), stuck_after_s=60) is True
    line = [e for e in logs if e.get("event") == "pg_stuck_transaction"]
    assert line and "UPDATE chat.messages_masked" in json.dumps(line[0]["sessions"], ensure_ascii=False)


# --- backend: every other builder -------------------------------------------


def test_rule_webhook_payload_and_notification_text_drop_session_sql():
    alert = Alert(
        alert_type=AlertType.STUCK_TRANSACTION, severity=AlertSeverity.CRITICAL, tenant_id="",
        message="stuck transaction: a database transaction has been idle for 64 s (limit 60 s)",
        details={"stuck_age_s": 64.2, "sessions": _stuck_sample().worst},
    )
    assert leaks(AlertService.build_payload(alert)) == []
    assert leaks(AlertService._notification_text(alert)) == []


@pytest.mark.parametrize("metric,value", [
    ("no_successful_turn", 7.0), ("error_rate", 12.5), ("dlp_block_spike", 6.0),
    ("provider_failure", 9.0), ("fallback_spike", 4.0), ("budget_threshold", 1.5),
    ("quota_exhausted", 100.0), ("stuck_transaction", 64.2),
])
def test_every_rule_message_is_clean(metric, value):
    pg_activity._set_last(_stuck_sample())
    rule = AlertRuleORM(id=str(uuid.uuid4()), name=metric, metric=metric, threshold=1.0,
                        window_minutes=5, scope="global", enabled=True)
    alert = AlertEvaluator._build_alert(rule, value)
    assert leaks(alert.message, alert.details, AlertService._notification_text(alert),
                 AlertService.build_payload(alert)) == []


def test_the_last_hop_scrubs_whatever_a_caller_passes(channels):
    """Defence in depth: even a caller that puts a secret in a notification
    (title, message or details) does not get it onto ntfy, e-mail or the
    webhook."""
    out = alert_channels.deliver(alert_channels.Notification(
        title=f"db {SECRET_DSN} down", message=f"last query: {HOSTILE_QUERY}", severity="critical",
        details={"query": HOSTILE_QUERY, "dsn": SECRET_DSN, "token": JWT, "nested": [{"key": API_KEY}]},
    ))
    assert out == {"ntfy": True, "email": True, "webhook": True}
    assert leaks(*_outbound(channels)) == []


def test_alert_service_scrubs_before_log_row_and_payload(session, logs):
    """Same at the AlertService boundary (log line, alert_events, payload)."""
    alert = Alert(
        alert_type=AlertType.PROVIDER_FAILURE, severity=AlertSeverity.WARNING, tenant_id="",
        message=f"provider down, dsn {SECRET_DSN}",
        details={"query": HOSTILE_QUERY, "error": f"401 for token {JWT}", "api_key": API_KEY},
    )
    event = AlertService().emit(alert, session=session)
    emitted = [e for e in logs if e.get("event") == "alert_emitted"]
    assert leaks(event.message, event.details_json or "", emitted, AlertService.build_payload(alert)) == []


# --- ops-watch ---------------------------------------------------------------


def _watch_rows() -> list[dict]:
    return [
        {"pid": 262, "state": "idle in transaction", "application_name": "psql",
         "wait_event_type": "Client", "xact_age_s": 64.2, "query": HOSTILE_QUERY},
    ]


def test_ops_watch_finding_is_numbers_not_sql():
    from app.ops.watch import classify_pg_activity

    [f] = classify_pg_activity("chat", _watch_rows(), stuck_after_s=60)
    assert "pid 262" in f.message and "psql" in f.message and "64 s" in f.message
    assert leaks(f.title, f.message) == []


def test_ops_watch_tick_sends_clean_notifications_and_logs_the_sql(monkeypatch, capsys):
    from app.ops import watch

    sent: list = []
    monkeypatch.setattr(watch, "docker_containers", lambda *a, **k: [])
    monkeypatch.setattr(watch, "probe_ready", lambda url: (200, {}, None))
    monkeypatch.setattr(watch, "disk_usage", lambda paths: {})
    monkeypatch.setattr(watch, "meminfo", lambda: (0, 0))
    monkeypatch.setattr(watch, "pg_activity_rows", lambda dsn: _watch_rows())
    monkeypatch.setattr(watch, "turn_counts", lambda dsn, w: (5, 0))
    monkeypatch.setattr(watch, "deliver", lambda n: sent.append(n) or {"ntfy": True})
    cfg = {
        "project": "p", "sock": "/nonexistent", "backend_url": "http://backend:8080",
        "disk_paths": [], "disk_pct": 90.0, "mem_pct": 90.0, "ignore_exited": (),
        "pg_dsns": [("chat", SECRET_DSN)], "pg_stuck_s": 60.0,
        "turn_window_min": 5, "turn_min_sample": 10, "turn_error_pct": 5.0,
    }
    watch.tick(watch.AlertState(cooldown_s=600), cfg)
    assert len(sent) == 1
    n = sent[0]
    assert leaks(n.title, n.message, n.details, n.payload()) == []
    # The operator still gets the query — in the watcher's own log, once.
    out = capsys.readouterr().out
    stuck_lines = [json.loads(line) for line in out.splitlines() if '"pg_stuck_transaction"' in line]
    assert len(stuck_lines) == 1 and "UPDATE chat.messages_masked" in json.dumps(stuck_lines[0], ensure_ascii=False)
    alert_lines = [json.loads(line) for line in out.splitlines() if '"msg": "alert"' in line]
    assert alert_lines and leaks(alert_lines) == []
