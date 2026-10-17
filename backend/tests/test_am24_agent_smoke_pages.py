"""AM24 §3.6 — a failed ``agent_market.*`` smoke check pages someone.

The core pager leaves the agent checks out (AM1b: Agent Market never pages the core channel,
test_am1b_smoke_bulkhead.py), and until AM24 nothing else read them: Agent Market's own pager read
Prometheus only, so a failed agent check paged nobody. Now its tick reads the same smoke record the
core reads and pages the agent topic. The fixture makes one agent check fail on purpose and asserts
the page goes out — and that the core channel still says nothing of it.
"""
from __future__ import annotations

import time

from app.ops import agent_market_alerts as ama
from app.ops import watch
from app.services.alert_pager import MemoryStore


def _record(checks):
    return {"epoch": time.time(), "at": "2026-10-18T23:30:02Z", "target": "live",
            "ok": not any(s == "FAIL" for _, s, _ in checks),
            "checks": [{"name": n, "status": s, "detail": d} for n, s, d in checks]}


FAILED = _record([("webchat.turn", "PASS", ""), ("agent_market.flag", "PASS", ""),
                  ("agent_market.turn", "FAIL", "asked qwen3.8-27b-fp8, no answer: the agent waits for its model")])


def _agent_state(sent):
    return watch.AlertState(ttl_s=90, store=MemoryStore(), send=lambda n: sent.append(n) or {"agent": True},
                            sender="ops-watch/agent-market")


def test_a_failed_agent_check_pages_the_agent_topic(monkeypatch):
    monkeypatch.setattr(ama, "firing_alerts", lambda url: {"data": {"alerts": []}})
    sent = []
    decisions = ama.agent_tick(_agent_state(sent), "http://prometheus:9090", FAILED)
    assert any(d.paged for d in decisions), decisions
    assert len(sent) == 1
    assert "Agent Market smoke check failed: agent_market.turn" in sent[0].title
    assert "the agent waits for its model" in sent[0].message
    # the core channel still says nothing of it (the bulkhead stands)
    assert watch.classify_smoke(FAILED, now=time.time(), max_age_h=26) == []


def test_a_run_with_no_agent_failure_pages_nothing(monkeypatch):
    monkeypatch.setattr(ama, "firing_alerts", lambda url: {"data": {"alerts": []}})
    sent = []
    green = _record([("webchat.turn", "PASS", ""), ("agent_market.turn", "PASS", "")])
    assert not any(d.paged for d in ama.agent_tick(_agent_state(sent), "http://prometheus:9090", green))
    assert sent == []
    assert ama.classify_agent_smoke(_record([("webchat.turn", "FAIL", "")])) == [], "a core failure is the core's"


def test_a_record_that_is_missing_or_not_a_record_is_the_cores_finding_not_this_ones():
    assert ama.classify_agent_smoke(None) == []
    assert ama.classify_agent_smoke({"ok": False}) == []


def test_the_watcher_hands_its_smoke_record_to_the_agent_tick():
    import inspect

    src = inspect.getsource(watch.main)
    assert "ama.agent_tick(agent, prom_url, smoke_record(cfg[\"smoke_file\"])" in src
