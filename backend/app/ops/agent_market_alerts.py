"""AM1c — Agent Market's own pager channel.

Its Prometheus alerts (``infra/prometheus/agent_market.yml``, every one with
``channel="agent-market"``) were dashboard-only: this stack has no
Alertmanager, so a runner dead at 3 am was invisible. ops-watch now reads the
firing ones from Prometheus every tick and hands them to a pager of their own:

* the same pager logic as the core (one page, one "recovered", reminders,
  the maintenance window) but its own Redis namespace, so the core pager never
  lists, reminds or closes an Agent Market incident and vice versa;
* delivered to ``AGENT_MARKET_NTFY_URL`` only (``deliver_agent_market``) —
  never the core channels, and a core incident never reaches that topic;
* Prometheus already waits each alert's ``for:``, so a firing alert pages at
  once; a check that fails (Prometheus down) is logged, never raised into the
  core tick.
* AM24 — and the daily smoke's own ``agent_market.*`` checks. The core pager
  leaves them out (AM1b: Agent Market never pages the core channel), and
  until AM24 nothing else read them: a failed agent check paged nobody. They
  are read from the same record the core reads (``smoke_last.json``) and page
  here, on the agent topic.

``python -m app.ops.agent_market_alerts test`` sends one ``[TEST]`` alert to
the agent topic and prints where it went.
"""

from __future__ import annotations

import json
import sys

import httpx

from app.ops.watch import AlertState, Finding, _log
from app.services import alert_catalog as cat
from app.services.alert_channels import Notification, deliver_agent_market
from app.services.alert_pager import Decision, FallbackStore, MemoryStore, RedisStore

CHANNEL = "agent-market"
AGENT_INCIDENT_PREFIX = "sran:alert:agent-market:incident:"
AGENT_LOCK_PREFIX = "sran:alert:agent-market:lock:"


def classify_agent_alerts(payload: dict | None) -> list[Finding]:
    """Prometheus ``/api/v1/alerts`` → one finding per firing Agent Market alert."""
    out: list[Finding] = []
    for a in ((payload or {}).get("data") or {}).get("alerts") or []:
        labels = a.get("labels") or {}
        if labels.get("channel") != CHANNEL or a.get("state") != "firing":
            continue
        name = labels.get("alertname") or "AgentMarketAlert"
        ann = a.get("annotations") or {}
        title = ann.get("summary") or name
        since = f" Firing since {a['activeAt'][:16].replace('T', ' ')} UTC." if a.get("activeAt") else ""
        message = (f"{ann.get('description') or title}.{since} Chat is not affected. "
                   "Grafana → Agent Market; to take it out: docs/agent-market/OPERATIONS.md (exit plan).")
        severity = cat.CRITICAL if labels.get("severity") == "critical" else cat.HIGH
        out.append(Finding(key=f"{CHANNEL}:{name}", title=title, message=message, severity=severity,
                           group=f"{CHANNEL}:{name}"))
    return out


def classify_agent_smoke(record: dict | None) -> list[Finding]:
    """AM24 — the daily smoke's failed ``agent_market.*`` checks → one finding on the agent pager. A record that is
    missing, stale or unreadable is the core's finding (the whole suite did not run), never repeated here."""
    if not isinstance(record, dict) or not isinstance(record.get("checks"), list):
        return []
    failed = [str(c.get("name")) for c in record["checks"]
              if isinstance(c, dict) and c.get("status") == "FAIL" and str(c.get("name") or "").startswith("agent_market.")]
    if not failed:
        return []
    detail = {str(c.get("name")): str(c.get("detail") or "")[:160] for c in record["checks"]
              if isinstance(c, dict) and str(c.get("name")) in failed}
    message = (f"The smoke run of {record.get('at') or 'unknown time'} on {record.get('target') or 'live'}: "
               + "; ".join(f"{n} — {detail[n]}" if detail[n] else n for n in failed)
               + ". Chat is not affected. Run scripts/smoke.sh by hand once it is fixed: the incident closes on a green run.")
    return [Finding(key=f"{CHANNEL}:smoke", title=f"Agent Market smoke check failed: {', '.join(failed[:3])}",
                    message=message, severity=cat.HIGH, group=f"{CHANNEL}:smoke")]


def firing_alerts(prom_url: str) -> dict:
    r = httpx.get(prom_url.rstrip("/") + "/api/v1/alerts", timeout=5.0)
    r.raise_for_status()
    return r.json()


def agent_redis_store(url: str | None = None, *, client=None) -> RedisStore:
    return RedisStore(url, client=client, incident_prefix=AGENT_INCIDENT_PREFIX, lock_prefix=AGENT_LOCK_PREFIX)


def agent_state(*, ttl_s: float) -> AlertState:
    """ops-watch's second pager: its own namespace, its own channel."""
    from app.core.config import settings

    store = FallbackStore(agent_redis_store(settings.redis_url), MemoryStore())
    return AlertState(ttl_s=ttl_s, store=store, send=deliver_agent_market, sender="ops-watch/agent-market")


def agent_tick(state: AlertState, prom_url: str, smoke: dict | None = None) -> list[Decision]:
    """One tick: firing Agent Market alerts, and the smoke's failed agent checks (AM24), → the agent pager. Never
    raises."""
    try:
        findings = classify_agent_alerts(firing_alerts(prom_url)) + classify_agent_smoke(smoke)
    except Exception as exc:  # noqa: BLE001 — a blind tick is logged, the core tick goes on
        _log("agent_market_alerts_unreadable", error=f"{type(exc).__name__}: {str(exc)[:120]}",
             note="Prometheus did not answer: Agent Market alerts are not checked this tick")
        return []
    try:
        decisions = state.step(findings)
    except Exception as exc:  # noqa: BLE001
        _log("agent_market_tick_failed", error=f"{type(exc).__name__}: {str(exc)[:120]}")
        return []
    if findings or any(d.paged for d in decisions):
        _log("agent_market_tick", open=[f.key for f in findings], paged=[d.group for d in decisions if d.paged])
    return decisions


def fire_test() -> dict[str, bool]:
    """One [TEST] alert on the agent topic (never the core channels)."""
    return deliver_agent_market(Notification(
        title="Agent Market pager test",
        message="A test of Agent Market's own alert topic. No action needed; chat is not affected.",
        severity=cat.HIGH, source="ops-watch/agent-market", tags=("incident",), test=True,
    ))


def main(argv: list[str]) -> int:
    if argv[1:2] == ["test"]:
        out = fire_test()
        print(json.dumps({"sent_to": out}))
        return 0 if out and all(out.values()) else 1
    print("usage: python -m app.ops.agent_market_alerts test")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
