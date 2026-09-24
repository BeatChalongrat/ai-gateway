"""FIX129 A3/A4 — the ops watcher's decisions (pure functions, fake Docker JSON)."""

from __future__ import annotations

from app.ops.watch import (
    AlertState,
    classify_containers,
    classify_disk,
    classify_memory,
    classify_pg_activity,
    classify_ready,
    parse_pg_dsns,
)


def test_stuck_transaction_seen_from_outside():
    rows = [
        {"pid": 31926, "state": "idle in transaction", "application_name": "", "wait_event_type": "Client",
         "xact_age_s": 1500.2, "query": "SELECT portal_users.id ... FOR UPDATE"},
        {"pid": 32003, "state": "active", "application_name": "", "wait_event_type": "Lock",
         "xact_age_s": 1499.0, "query": "SELECT portal_users.id ... FOR UPDATE"},
    ]
    assert classify_pg_activity("gateway", rows, stuck_after_s=60) [0].key == "pg:gateway:stuck"
    f = classify_pg_activity("gateway", rows, stuck_after_s=60)[0]
    # FIX131: the title says what it means for users; the incident is the
    # stuck-transaction group the backend rule reports into as well.
    assert f.title == "Chat may hang: a database transaction is stuck (gateway, 25 min)"
    assert f.incident == "db:gateway:stuck" and f.severity == "warning"
    assert "pid 31926 idle in transaction" in f.message and "2 session(s)" in f.message
    assert classify_pg_activity("gateway", rows, stuck_after_s=2000) == []
    assert classify_pg_activity("chat", [], stuck_after_s=60) == []


def test_turn_rules_from_the_chat_db():
    from app.ops.watch import classify_turns

    kw = dict(window_min=5, min_sample=10, max_error_pct=5.0)
    assert classify_turns(0, 0, **kw) == []                       # a quiet night is not an outage
    assert classify_turns(3, 0, **kw) == []
    f = classify_turns(0, 12, **kw)
    assert [x.key for x in f] == ["turns:none_succeeded", "turns:error_rate"]
    assert f[0].message.startswith("12 turn(s) attempted in the last 5 min, none completed (12 failed).")
    # FIX131: severity by impact — nothing succeeds = users cannot chat
    # (critical); an error rate is degraded (warning), however high.
    assert f[0].severity == "critical"
    assert f[1].severity == "warning" and "12 of 12 turns failed" in f[1].message
    assert classify_turns(8, 1, **kw) == []                       # below the sample floor
    g = classify_turns(93, 7, **kw)
    assert [x.key for x in g] == ["turns:error_rate"] and g[0].severity == "warning"
    assert classify_turns(96, 4, **kw) == []                      # 4 % is under the limit


def test_pg_dsn_spec_parsing():
    assert parse_pg_dsns("") == []
    assert parse_pg_dsns("chat=postgresql://u:p@h:5432/db;gateway=postgresql://x@y/z") == [
        ("chat", "postgresql://u:p@h:5432/db"), ("gateway", "postgresql://x@y/z"),
    ]
    assert parse_pg_dsns("broken") == []


def _c(name: str, state: str, status: str, **labels) -> dict:
    return {
        "Id": "abc123def456", "Names": [f"/{name}"], "State": state, "Status": status,
        "Labels": {"com.docker.compose.project": "ai-gateway",
                   "com.docker.compose.service": name.replace("ai-gateway-", "").rstrip("-1"), **labels},
    }


def test_unhealthy_restarting_dead_and_exited_are_findings():
    items = [
        _c("ai-gateway-backend-1", "running", "Up 2 hours (unhealthy)"),
        _c("ai-gateway-gateway-1", "running", "Up 2 hours (healthy)"),
        _c("ai-gateway-chat-edge-api", "restarting", "Restarting (1) 5 seconds ago"),
        _c("ai-gateway-web-chat", "exited", "Exited (137) 3 minutes ago"),
        _c("ai-gateway-redis-1", "dead", "Dead"),
        _c("ai-gateway-backup-run-1", "exited", "Exited (0) 1 hour ago", **{"com.docker.compose.oneoff": "True"}),
        _c("ai-gateway-migrate-1", "exited", "Exited (0) 1 day ago"),
    ]
    found = classify_containers(items, ignore_exited=("ai-gateway-migrate",))
    keys = {f.key: f for f in found}
    assert set(keys) == {
        "container:ai-gateway-backend-1", "container:ai-gateway-chat-edge-api",
        "container:ai-gateway-web-chat", "container:ai-gateway-redis-1",
    }
    assert keys["container:ai-gateway-backend-1"].title.endswith("is unhealthy")
    assert "Up 2 hours (unhealthy)" in keys["container:ai-gateway-backend-1"].message
    assert keys["container:ai-gateway-chat-edge-api"].title.endswith("is restarting")
    assert keys["container:ai-gateway-web-chat"].title.endswith("has exited")


def test_disk_and_memory_thresholds():
    gib = 2**30
    assert classify_disk({"/host/docker": (100 * gib, 20 * gib)}, max_pct=90) == []
    f = classify_disk({"/host/docker": (100 * gib, 5 * gib)}, max_pct=90)
    assert len(f) == 1 and "95 % full" in f[0].title and "5.0 GiB free" in f[0].message
    assert classify_memory(8_000_000, 2_000_000, max_pct=90) == []
    m = classify_memory(8_000_000, 400_000, max_pct=90)
    assert len(m) == 1 and m[0].key == "memory" and "95 % used" in m[0].title
    assert classify_memory(0, 0, max_pct=90) == []


def test_ready_probe_outcomes():
    assert classify_ready(200, {"status": "ready"}, None) == []
    f = classify_ready(503, {"failures": ["stuck_transaction", "database"]}, None)
    assert f[0].key == "backend:ready" and "stuck_transaction, database" in f[0].message
    g = classify_ready(None, None, "ReadTimeout: timed out")
    assert "does not answer" in g[0].title and "ReadTimeout" in g[0].message


def test_alert_state_pages_once_and_recovers_once():
    """FIX131: the watcher no longer re-announces an open finding every 10
    minutes (OPS_WATCH_COOLDOWN); the pager pages once per incident, stays
    quiet while it is open, and sends one "recovered" when it closes."""
    from app.services.alert_pager import MemoryStore

    clock = {"t": 1000.0}
    sent: list = []
    state = AlertState(store=MemoryStore(now=lambda: clock["t"]), now=lambda: clock["t"])
    state.pager._send = lambda n: sent.append(n) or {"ntfy": True}
    f = classify_ready(503, {"failures": ["x"]}, None)
    state.step(f)
    assert [n.title for n in sent] == ["Chat is down: the backend is not ready"]
    assert sent[0].severity == "critical"
    clock["t"] += 60
    state.step(f)
    clock["t"] += 600
    state.step(f)
    assert len(sent) == 1                                # open incident: quiet (no 10-min re-announce)
    clock["t"] += 30
    state.step([])
    assert len(sent) == 2 and sent[1].title == "Recovered: Chat is down: the backend is not ready"
    assert sent[1].severity == "low"
    state.step(f)
    assert len(sent) == 3                                # a fresh incident pages at once
