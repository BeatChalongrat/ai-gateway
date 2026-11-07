"""AM1b §0.4 — Agent Market never pages the core channel.

The daily smoke suite includes the ``agent_market.*`` checks (they gate a
deploy), but a failure there is the agent's problem: it shows on the agent's
own dashboard and alerts (channel ``agent-market``), and the core pager
(ops-watch → the owner's topics) stays quiet about it.
"""
from __future__ import annotations

import time

from app.ops import watch


def _record(checks, ok=None):
    fails = [c for c in checks if c[1] == "FAIL"]
    return {"epoch": time.time(), "at": "2026-09-26T03:00:00Z", "target": "live",
            "ok": (not fails) if ok is None else ok,
            "checks": [{"name": n, "status": s} for n, s in checks]}


def test_an_agent_market_failure_alone_does_not_page_the_core_channel():
    rec = _record([("webchat.turn", "PASS"), ("agent_market.runner", "FAIL"), ("agent_market.flag", "FAIL")])
    assert watch.classify_smoke(rec, now=time.time(), max_age_h=26) == []


def test_a_core_failure_still_pages_and_names_only_core_checks():
    rec = _record([("webchat.turn", "FAIL"), ("agent_market.runner", "FAIL")])
    findings = watch.classify_smoke(rec, now=time.time(), max_age_h=26)
    assert len(findings) == 1
    assert "webchat.turn" in findings[0].message
    assert "agent_market" not in findings[0].message
