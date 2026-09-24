"""FIX131 D — a pager you can trust: one incident = one page (+ one "recovered").

NIGHT3 (2026-09-23): one stuck transaction produced FOUR pushes — the
backend rule "Transaction stuck longer than 60 s" (p4), ops-watch "backend
is not ready: /ready answered 503: stuck_transaction" (p5), "transaction
stuck 80 s on gateway" (p5) and "… on chat" (p5); a planned backend reload
paged p5; one failed turn in a quiet 5 min paged twice (the rule AND the
watcher's twin). Before FIX131 each sender kept its own cooldown and graded
severity by its own rule; there was no shared state, no debounce and no
maintenance window.

These tests drive the real senders' code paths (the backend's
AlertEvaluator + AlertService, the ops watcher's AlertState/classify_*) into
ONE shared store — a MemoryStore here, Redis in production (the RedisStore
path is covered with a fake client at the end) — and count what is sent.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.config import settings
from app.core.db import Base
from app.models.alert_rule import AlertEventORM, AlertRuleORM
from app.models.request_log import RequestLogORM
from app.ops import watch
from app.services import alert_catalog as cat
from app.services import alert_channels, alert_pager, pg_activity
from app.services.alert_evaluator import AlertEvaluator
from app.services.alert_pager import FallbackStore, MemoryStore, Pager, RedisStore, Signal


class Clock:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture()
def clock():
    return Clock()


@pytest.fixture()
def sent(monkeypatch):
    """Everything any sender hands to the channels (ntfy configured, fake)."""
    out: list[alert_channels.Notification] = []
    monkeypatch.setattr(settings, "alert_ntfy_url", "https://ntfy.example/owner")
    monkeypatch.setattr(alert_channels, "deliver", lambda n: out.append(n) or {"ntfy": True})
    monkeypatch.setattr(watch, "deliver", lambda n: out.append(n) or {"ntfy": True})
    return out


@pytest.fixture()
def shared(monkeypatch, clock):
    """The store both senders share (Redis in production), on one clock."""
    store = MemoryStore(now=clock)
    monkeypatch.setattr(alert_pager, "_store", store)
    real_get_pager = alert_pager.get_pager
    monkeypatch.setattr(alert_pager, "get_pager", lambda **kw: real_get_pager(now=clock, **kw))
    return store


@pytest.fixture(autouse=True)
def _no_sample_leaks():
    yield
    pg_activity._last = None


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


def _rule(session, metric: str, threshold: float, window: int = 5) -> AlertRuleORM:
    r = AlertRuleORM(id=str(uuid.uuid4()), name=metric, metric=metric, threshold=threshold,
                     window_minutes=window, scope="global", enabled=True)
    session.add(r)
    session.commit()
    return r


def _request(session, status: str) -> None:
    session.add(RequestLogORM(
        request_id=str(uuid.uuid4()), tenant_id="t", project_id="p", provider="openai", model="m",
        status=status, policy_action="allow", latency_ms=1, prompt_tokens=1, completion_tokens=1,
        total_tokens=2, estimated_cost_usd=0.0, created_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    ))
    session.commit()


def _stuck_sample(age: float = 80.0) -> pg_activity.ActivitySample:
    return pg_activity.ActivitySample(
        at=time.time(), idle_in_transaction=1, idle_in_transaction_max_age_s=age,
        worst=[{"pid": 31926, "state": "idle in transaction", "application_name": "sran-backend-async",
                "wait": "Client", "xact_age_s": age, "query": "SELECT … FOR UPDATE"}],
    )


def _watcher(clock) -> watch.AlertState:
    return watch.AlertState(now=clock, ttl_s=90)


_STUCK_ROWS = [{"pid": 31926, "state": "idle in transaction", "application_name": "sran-backend-async",
                "wait_event_type": "Client", "xact_age_s": 80.0, "query": "SELECT … FOR UPDATE"}]


# ---------------------------------------------------------------------------
# 1. NIGHT3's four pushes: one incident per target, across two senders
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stuck_transaction_backend_rule_and_ops_watch_page_once(session, sent, shared, clock):
    rule = _rule(session, "stuck_transaction", 60.0, window=1)
    pg_activity._set_last(_stuck_sample(80.0))
    await AlertEvaluator().evaluate_all(session)                   # sender 1: the backend rule
    w = _watcher(clock)
    ops = (watch.classify_pg_activity("gateway", _STUCK_ROWS, stuck_after_s=60)
           + watch.classify_ready(503, {"failures": ["stuck_transaction"]}, None, debounce_s=90))
    w.step(ops)                                                      # sender 2: ops-watch, same incident
    for _ in range(20):                                              # 10 minutes of both senders re-reporting
        clock.t += 30
        w.step(ops)
        await AlertEvaluator().evaluate_all(session)
    stuck_pages = [n for n in sent if "database transaction is stuck (gateway" in n.title]
    assert len(stuck_pages) == 1, [n.title for n in sent]
    assert stuck_pages[0].severity == "warning"                     # degraded, not "users cannot chat"
    assert len(sent) == 1

    # Recovery: the rule clears, ops-watch still sees it → still open; then
    # ops-watch clears too → ONE "recovered".
    pg_activity._set_last(pg_activity.ActivitySample(at=time.time()))
    await AlertEvaluator().evaluate_all(session)
    assert len(sent) == 1
    clock.t += 30
    w.step([])
    assert [n.title for n in sent[1:]] == ["Recovered: " + stuck_pages[0].title]
    assert sent[1].severity == "low"
    assert session.query(AlertEventORM).filter(AlertEventORM.rule_id == rule.id).count() >= 1


def test_the_chat_database_is_its_own_incident(sent, shared, clock):
    w = _watcher(clock)
    w.step(watch.classify_pg_activity("gateway", _STUCK_ROWS, stuck_after_s=60)
           + watch.classify_pg_activity("chat", _STUCK_ROWS, stuck_after_s=60))
    assert sorted(n.title.split("(")[1].split(",")[0] for n in sent) == ["chat", "gateway"]


# ---------------------------------------------------------------------------
# 2. Severity by impact
# ---------------------------------------------------------------------------

def test_severity_by_impact():
    kw = dict(window_min=5, min_sample=10, max_error_pct=5.0, min_attempts=3)
    [one] = watch.classify_turns(0, 1, **kw)
    assert one.severity == "none"                                   # one failed turn: log only
    [three] = watch.classify_turns(0, 3, **kw)
    assert three.severity == "critical"                             # users cannot chat
    rate = [f for f in watch.classify_turns(80, 20, **kw) if f.key == "turns:error_rate"][0]
    assert rate.severity == "warning"                               # degraded
    assert cat.container_text("backend", "is unhealthy", "b", "s")[0] == "critical"
    assert cat.container_text("chat-edge-api", "has exited", "c", "s")[0] == "critical"
    assert cat.container_text("admin-ui", "has exited", "a", "s")[0] == "warning"
    assert cat.container_text("backup", "has exited", "a", "s")[0] == "warning"
    assert watch.classify_ready(None, None, "ReadTimeout")[0].severity == "critical"
    assert watch.classify_ready(503, {"failures": ["stuck_transaction"]}, None)[0].severity == "warning"
    assert watch.classify_disk({"/d": (100, 5)}, max_pct=90)[0].severity == "warning"
    assert watch.classify_memory(100, 5, max_pct=90)[0].severity == "warning"
    assert all(f.severity == "warning" for f in watch.classify_backup(None, None, now=1e9, max_age_h=26))


def test_ready_503_for_a_stuck_transaction_joins_the_stuck_incident():
    [f] = watch.classify_ready(503, {"failures": ["stuck_transaction"]}, None, debounce_s=90)
    assert f.incident == "db:gateway:stuck"
    [g] = watch.classify_ready(503, {"failures": ["stuck_transaction", "database"]}, None, debounce_s=90)
    assert g.incident == "service:backend" and g.debounce_s == 90
    [c] = watch.classify_containers([{"Names": ["/ai-gateway-backend-1"], "State": "running",
                                      "Status": "Up 1 h (unhealthy)",
                                      "Labels": {"com.docker.compose.service": "backend"}}], debounce_s=90)
    assert c.incident == g.incident == "service:backend"           # container + probe = one incident


@pytest.mark.asyncio
async def test_one_failed_turn_in_a_quiet_window_pages_nobody(session, sent, shared, clock):
    """NIGHT3: one failed turn paged twice (rule p4 + watcher p5)."""
    _rule(session, "no_successful_turn", 1.0)
    _request(session, "provider_error")
    await AlertEvaluator().evaluate_all(session)
    w = _watcher(clock)
    w.step(watch.classify_turns(0, 1, window_min=5, min_sample=10, max_error_pct=5.0, min_attempts=3))
    assert sent == []
    # … and three failures (an outage) page once, from whichever sender sees it first.
    _request(session, "provider_error")
    _request(session, "provider_error")
    await AlertEvaluator().evaluate_all(session)                   # inside the rule's cooldown: escalation
    clock.t += 30
    w.step(watch.classify_turns(0, 3, window_min=5, min_sample=10, max_error_pct=5.0, min_attempts=3))
    assert len(sent) == 1 and sent[0].severity == "critical"
    assert sent[0].title.startswith("Chat is failing: no reply succeeded in 5 min")


# ---------------------------------------------------------------------------
# 3. Debounce: a restart that heals does not page
# ---------------------------------------------------------------------------

def _backend_down(debounce=90):
    return watch.classify_ready(None, None, "ConnectError: refused", debounce_s=debounce)


def test_a_restart_that_heals_within_the_debounce_pages_nobody(sent, shared, clock):
    w = _watcher(clock)
    w.step(_backend_down())
    clock.t += 30
    w.step(_backend_down())
    clock.t += 30
    w.step([])                                                       # healed after ~60 s
    assert sent == []


def test_an_outage_past_the_debounce_pages_once(sent, shared, clock):
    w = _watcher(clock)
    for _ in range(3):                                               # t = 0, 30, 60: held
        w.step(_backend_down())
        clock.t += 30
    assert sent == []
    w.step(_backend_down())                                          # t = 90: pages
    assert [n.title for n in sent] == ["Chat is down: the backend does not answer"]
    assert sent[0].severity == "critical"


# ---------------------------------------------------------------------------
# 4. Maintenance window
# ---------------------------------------------------------------------------

def test_maintenance_window_holds_pages_and_what_outlives_it_pages(sent, shared, clock):
    from app.ops import maintenance

    lines: list[str] = []
    assert maintenance.main(["start", "10", "deploy", "FIX131"], store=shared, out=lines.append) == 0
    assert "maintenance window ON" in lines[-1]
    w = _watcher(clock)
    for _ in range(10):                                              # 5 min of "backend down" during the deploy
        w.step(_backend_down())
        clock.t += 30
    assert sent == []
    clock.t += 300                                                   # the window (10 min) has ended
    w.step(_backend_down())
    assert len(sent) == 1 and sent[0].severity == "critical"
    assert "began during maintenance — deploy FIX131" in sent[0].message


def test_a_deploy_inside_the_window_that_heals_sends_nothing(sent, shared, clock):
    from app.ops import maintenance

    maintenance.main(["start", "15", "deploy"], store=shared, out=lambda s: None)
    w = _watcher(clock)
    for _ in range(6):
        w.step(_backend_down())
        clock.t += 30
    w.step([])
    assert maintenance.main(["stop"], store=shared, out=lambda s: None) == 0
    assert shared.maintenance() is None
    assert sent == []                                                # no page, no "recovered"


def test_maintenance_cli_usage_and_cap(shared):
    out: list[str] = []
    assert alert_pager_main(["start"], shared, out) == 2
    assert alert_pager_main(["start", "10000", "long", "job"], shared, out) == 0
    assert "capped at 480 min" in out[-2]
    assert alert_pager_main(["status"], shared, out) == 0 and '"reason": "long job"' in out[-1]


def alert_pager_main(argv, store, out):
    from app.ops import maintenance

    return maintenance.main(argv, store=store, out=out.append)


def test_maintenance_cli_says_so_when_redis_is_down():
    from app.ops import maintenance

    class Down:
        def set_maintenance(self, *a):
            raise ConnectionError("refused")

    out: list[str] = []
    assert maintenance.main(["start", "10", "deploy"], store=Down(), out=out.append) == 1
    assert "NOT set" in out[-1]


# ---------------------------------------------------------------------------
# 5. Re-notify only on escalation or after the reminder interval
# ---------------------------------------------------------------------------

def _sig(sev="warning", source="s1", group="g", debounce=0.0):
    return Signal(group=group, source=source, severity=sev, title=f"t-{sev}", body="b", debounce_s=debounce)


def test_reminder_after_the_interval_and_escalation(clock):
    sent: list = []
    p = Pager(MemoryStore(now=clock), send=lambda n: sent.append(n) or {"ntfy": True}, now=clock,
              reminder_s=4 * 3600, reminder_high_s=24 * 3600)
    assert p.fire(_sig("warning")).action == "page"
    clock.t += 3 * 3600
    assert p.fire(_sig("warning")).action == "grouped"
    clock.t += 3600
    assert p.fire(_sig("warning")).action == "grouped"              # high: 24 h, not 4 h
    assert p.fire(_sig("critical", source="s2")).action == "escalate"
    assert sent[-1].severity == "critical" and "Worse than first reported" in sent[-1].message
    clock.t += 4 * 3600
    d = p.fire(_sig("critical", source="s2"))
    assert d.action == "remind" and sent[-1].title == "Still: t-critical"
    assert len(sent) == 3


def test_a_failed_delivery_is_retried_on_the_next_sighting(clock):
    outcomes = [{"ntfy": False}, {"ntfy": True}]
    sent: list = []
    p = Pager(MemoryStore(now=clock), send=lambda n: sent.append(n) or outcomes.pop(0), now=clock)
    assert p.fire(_sig()).action == "page"
    assert p.fire(_sig()).action == "page"                          # not marked paged: tried again
    assert p.fire(_sig()).action == "grouped"
    assert len(sent) == 2


def test_a_sender_that_already_paged_is_not_paged_again_but_recovers_once(clock):
    sent: list = []
    p = Pager(MemoryStore(now=clock), send=lambda n: sent.append(n) or {"ntfy": True}, now=clock)
    sig = Signal(group="backup", source="ops-watch:backup:failed", severity="warning", title="Backups are failing",
                 body="b", notified_elsewhere=True)
    assert p.fire(sig).action == "grouped"
    assert sent == []
    assert p.clear("backup", "ops-watch:backup:failed").action == "recovered"
    assert [n.title for n in sent] == ["Recovered: Backups are failing"]


def test_expired_sightings_close_with_an_honest_note(clock):
    sent: list = []
    p = Pager(MemoryStore(now=clock), send=lambda n: sent.append(n) or {"ntfy": True}, now=clock)
    p.fire(Signal(group="g", source="rule:x", severity="warning", title="T", body="b", ttl_s=120))
    clock.t += 121
    [d] = p.sweep()
    assert d.action == "expired" and sent[-1].title == "No longer reported: T"


def test_a_sender_cannot_clear_what_it_did_not_report(clock):
    p = Pager(MemoryStore(now=clock), send=lambda n: {"ntfy": True}, now=clock)
    p.fire(_sig(source="ops-watch:turns:none_succeeded", group="turns:none_succeeded"))
    assert p.clear("turns:none_succeeded", "rule:abc").action == "open"


# ---------------------------------------------------------------------------
# 6. The shared store is Redis: two processes, two RedisStore clients, one page
# ---------------------------------------------------------------------------

class FakeRedis:
    """Just the commands RedisStore uses, with expiry on a fake clock."""

    def __init__(self, clock) -> None:
        self.clock = clock
        self.d: dict[str, tuple[str, float | None]] = {}

    def _get(self, k):
        v = self.d.get(k)
        if v is None:
            return None
        if v[1] is not None and self.clock() >= v[1]:
            del self.d[k]
            return None
        return v[0]

    def set(self, k, v, nx=False, px=None, ex=None):
        if nx and self._get(k) is not None:
            return None
        exp = self.clock() + (px / 1000 if px else ex) if (px or ex) else None
        self.d[k] = (v, exp)
        return True

    def get(self, k):
        return self._get(k)

    def delete(self, k):
        return 1 if self.d.pop(k, None) is not None else 0

    def ttl(self, k):
        v = self.d.get(k)
        return int(v[1] - self.clock()) if v and v[1] else -1

    def scan_iter(self, match, count=100):
        prefix = match.rstrip("*")
        return [k for k in list(self.d) if k.startswith(prefix) and self._get(k) is not None]


def test_two_processes_share_one_incident_through_redis(clock):
    fake = FakeRedis(clock)
    sent: list = []
    backend = Pager(RedisStore(client=fake), send=lambda n: sent.append(("backend", n)) or {"ntfy": True}, now=clock)
    opswatch = Pager(RedisStore(client=fake), send=lambda n: sent.append(("ops", n)) or {"ntfy": True}, now=clock)
    assert backend.fire(_sig(source="rule:1", group="db:gateway:stuck")).action == "page"
    assert opswatch.fire(_sig(source="ops-watch:pg:gateway:stuck", group="db:gateway:stuck")).action == "grouped"
    assert [k for k in fake.d if k.startswith("sran:alert:incident:")] == ["sran:alert:incident:db:gateway:stuck"]
    assert not [k for k in fake.d if k.startswith("sran:alert:lock:")]      # locks released
    backend.clear("db:gateway:stuck", "rule:1")
    opswatch.clear("db:gateway:stuck", "ops-watch:pg:gateway:stuck")
    assert [who for who, _ in sent] == ["backend", "ops"]                   # one page, one recovered
    RedisStore(client=fake).set_maintenance({"reason": "deploy"}, 600)
    assert opswatch.maintenance()["reason"] == "deploy"
    clock.t += 601
    assert opswatch.maintenance() is None                                   # the TTL ends a forgotten window


def test_redis_down_falls_back_to_memory_and_still_pages_once(clock):
    class Down:
        def __getattr__(self, name):
            def _raise(*a, **k):
                raise ConnectionError("redis unreachable")
            return _raise

    store = FallbackStore(RedisStore(client=Down()), MemoryStore(now=clock), now=clock)
    sent: list = []
    p = Pager(store, send=lambda n: sent.append(n) or {"ntfy": True}, now=clock)
    assert p.fire(_sig()).action == "page"
    assert p.fire(_sig()).action == "grouped"
    assert len(sent) == 1


def test_ops_watch_state_file_keeps_the_incident_across_a_restart(tmp_path, clock, sent):
    store = MemoryStore(now=clock)
    w = watch.AlertState(store=store, now=clock)
    w.step(watch.classify_disk({"/host/docker": (100 * 2**30, 5 * 2**30)}, max_pct=90))
    assert len(sent) == 1
    path = str(tmp_path / "state.json")
    w.save(path)
    w2 = watch.AlertState(store=MemoryStore(now=clock), now=clock)      # the watcher restarted, Redis down
    w2.load(path)
    clock.t += 30
    w2.step(watch.classify_disk({"/host/docker": (100 * 2**30, 5 * 2**30)}, max_pct=90))
    assert len(sent) == 1                                               # not re-paged
    clock.t += 30
    w2.step([])
    assert sent[-1].title.startswith("Recovered: Server disk is 95 % full")
