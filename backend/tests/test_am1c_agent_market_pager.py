"""AM1c — Agent Market pages on a topic of its own.

Its Prometheus alerts (channel="agent-market") were dashboard-only: a runner
dead at 3 am was invisible. ops-watch now reads them from Prometheus and pages
them through the same pager (one page, one "recovered") to
AGENT_MARKET_NTFY_URL only — never the core channels — and the core pager
never sees, reminds or recovers an Agent Market incident.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import settings
from app.ops import agent_market_alerts as ama
from app.ops import watch
from app.services import alert_catalog as cat
from app.services import alert_channels
from app.services.alert_channels import Notification
from app.services.alert_pager import MemoryStore, Pager, RedisStore, Signal
from tests.test_fix131_pager import Clock, FakeRedis

CORE = "https://ntfy.example/core-topic"
HOOK = "https://hook.example/core"
TEST = "https://ntfy.example/test-topic"
AGENT = "https://ntfy.example/agent-topic"

PROM = {
    "status": "success",
    "data": {
        "alerts": [
            {
                "labels": {"alertname": "AgentMarketRunnerDown", "severity": "warning", "channel": "agent-market"},
                "annotations": {"summary": "Agent Market runner is not answering /metrics (runs wait; chat is unaffected)"},
                "state": "firing",
                "activeAt": "2026-09-26T03:00:00Z",
            },
            {
                "labels": {"alertname": "AgentMarketQueueStuck", "severity": "warning", "channel": "agent-market"},
                "annotations": {"summary": "Agent Market: a run has waited in the queue for more than 10 minutes"},
                "state": "pending",
            },
            {"labels": {"alertname": "TargetDown", "job": "gateway"}, "annotations": {"summary": "core"}, "state": "firing"},
        ]
    },
}


@pytest.fixture()
def captured(monkeypatch):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"id": "x"})

    real_client = httpx.Client

    def _client(**kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real_client(**kw)

    monkeypatch.setattr(alert_channels.httpx, "Client", _client)
    monkeypatch.setattr(settings, "alert_ntfy_url", CORE)
    monkeypatch.setattr(settings, "alert_ntfy_token", "core-token")
    monkeypatch.setattr(settings, "alert_email_to", "")
    monkeypatch.setattr(settings, "alert_webhook_url", HOOK)
    monkeypatch.setattr(settings, "alert_test_ntfy_url", TEST)
    monkeypatch.setattr(settings, "alert_environment", "")
    monkeypatch.setattr(settings, "agent_market_ntfy_url", AGENT)
    return calls


def test_only_firing_agent_market_alerts_are_findings():
    found = ama.classify_agent_alerts(PROM)
    assert [f.key for f in found] == ["agent-market:AgentMarketRunnerDown"]
    assert found[0].incident == "agent-market:AgentMarketRunnerDown"
    assert found[0].severity == cat.HIGH
    assert "runner is not answering" in found[0].title


def test_an_agent_page_goes_to_the_agent_topic_and_nowhere_else(captured):
    out = alert_channels.deliver_agent_market(Notification(title="Agent Market runner is down", message="m"))
    assert out == {"ntfy-agent-market": True}
    assert [str(r.url) for r in captured] == [AGENT]
    assert "authorization" not in captured[0].headers  # the core topic's token stays with the core topic


def test_a_core_page_never_reaches_the_agent_topic(captured):
    alert_channels.deliver(Notification(title="backend is not ready", message="m"))
    assert AGENT not in [str(r.url) for r in captured]
    assert [str(r.url) for r in captured] == [CORE, HOOK]


def test_the_test_fire_is_labelled_and_lands_on_the_agent_topic_only(captured):
    out = ama.fire_test()
    assert out == {"ntfy-agent-market": True}
    assert [str(r.url) for r in captured] == [AGENT]
    assert captured[0].headers["Title"].startswith("[TEST]")
    assert captured[0].content.decode().startswith("[TEST]")


def test_in_a_test_environment_agent_alerts_go_to_the_test_topic(captured, monkeypatch):
    monkeypatch.setattr(settings, "alert_environment", "test-stack")
    alert_channels.deliver_agent_market(Notification(title="Agent Market runner is down", message="m"))
    assert [str(r.url) for r in captured] == [TEST]


def test_no_agent_topic_means_no_agent_page(captured, monkeypatch):
    monkeypatch.setattr(settings, "agent_market_ntfy_url", "")
    assert alert_channels.deliver_agent_market(Notification(title="t", message="m")) == {}
    assert captured == []  # and never the core channels instead


def test_the_core_pager_never_sees_or_pages_an_agent_incident():
    clock = Clock()
    fake = FakeRedis(clock)
    agent_sent: list = []
    core_sent: list = []
    agent = Pager(ama.agent_redis_store(client=fake), send=lambda n: agent_sent.append(n) or {"ntfy-agent-market": True},
                  now=clock, sender="ops-watch/agent-market")
    core = Pager(RedisStore(client=fake), send=lambda n: core_sent.append(n) or {"ntfy": True}, now=clock,
                 reminder_s=60, reminder_high_s=60)
    d = agent.fire(Signal(group="agent-market:AgentMarketRunnerDown", source="ops-watch:agent-market:AgentMarketRunnerDown",
                          severity=cat.HIGH, title="runner down", body="b", ttl_s=600))
    assert d.action == "page" and len(agent_sent) == 1
    assert RedisStore(client=fake).groups() == []  # the core pager's view of open incidents
    clock.t += 7200  # far past any reminder
    assert core.sweep() == []
    assert core_sent == []


def test_ops_watch_pages_an_agent_alert_once_then_says_recovered():
    clock = Clock()
    sent: list = []
    state = watch.AlertState(now=clock, store=MemoryStore(now=clock),
                             send=lambda n: sent.append(n) or {"ntfy-agent-market": True}, sender="ops-watch/agent-market")
    firing = ama.classify_agent_alerts(PROM)
    state.step(firing)
    clock.t += 30
    state.step(firing)  # still firing: grouped, no second page
    clock.t += 30
    state.step([])  # resolved in Prometheus
    assert len(sent) == 2
    assert "runner is not answering" in sent[0].title
    assert sent[1].severity == cat.LOW  # the "recovered" note


def test_a_failing_agent_check_never_breaks_the_core_tick(monkeypatch):
    def down(url):
        raise httpx.ConnectError("prometheus is down")

    monkeypatch.setattr(ama, "firing_alerts", down)
    state = watch.AlertState(store=MemoryStore(), send=lambda n: {"ntfy-agent-market": True})
    assert ama.agent_tick(state, "http://prometheus:9090") == []
