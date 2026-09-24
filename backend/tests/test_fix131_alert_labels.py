"""FIX131 D4 — a test alert is labelled [TEST] and never reaches the owner.

NIGHT3: a test-stack page looked exactly like a real one (no environment in
the text), and on 2026-09-23 17 fake "test-rule" alerts reached the owner's
topic from a test lane. Now:

* ``ALERT_ENVIRONMENT`` labels every title and body of a deployment that is
  not the real system; a test environment (test / testing / ci / lane /
  test-stack) also ROUTES every alert to ``ALERT_TEST_NTFY_URL`` only —
  never ``ALERT_NTFY_URL``, its token, e-mail or the global webhook;
* a test fire (``Notification(test=True)``, ``python -m app.ops.fire_test_alert``)
  is [TEST] and goes to the test topic, whatever the environment.

``infra/backup/tests/test_notify.sh`` proves the same for the backup job.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import settings
from app.services import alert_channels
from app.services.alert_channels import Notification

OWNER = "https://ntfy.example/owner-topic"
TEST_TOPIC = "https://ntfy.example/throwaway-test-topic"


@pytest.fixture()
def wire(monkeypatch):
    """Every HTTP request any channel makes, and every e-mail."""
    calls: list[httpx.Request] = []
    mails: list[dict] = []
    real_client = httpx.Client

    def _client(**kw):
        kw["transport"] = httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200, json={}))
        return real_client(**kw)

    monkeypatch.setattr(alert_channels.httpx, "Client", _client)
    import app.services.email.sender as sender

    monkeypatch.setattr(sender, "send_email", lambda **kw: mails.append(kw) or type("R", (), {"delivered": True})())
    monkeypatch.setattr(settings, "alert_ntfy_url", OWNER)
    monkeypatch.setattr(settings, "alert_ntfy_token", "tk_owner_token_0123456789abcdef")
    monkeypatch.setattr(settings, "alert_email_to", "owner@example.com")
    monkeypatch.setattr(settings, "alert_webhook_url", "https://hooks.example/owner")
    return calls, mails


def test_test_environment_labels_and_routes_to_the_test_topic_only(wire, monkeypatch):
    calls, mails = wire
    monkeypatch.setattr(settings, "alert_environment", "test")
    monkeypatch.setattr(settings, "alert_test_ntfy_url", TEST_TOPIC)
    out = alert_channels.deliver(Notification(title="Chat is down", message="the backend does not answer",
                                              severity="critical"))
    assert out == {"ntfy-test": True}
    assert [str(r.url) for r in calls] == [TEST_TOPIC]
    assert mails == []
    r = calls[0]
    assert r.headers["Title"] == "[TEST] Chat is down"
    assert r.content.decode() == "[TEST] the backend does not answer"
    assert "Authorization" not in r.headers                         # the owner's token never goes there
    assert "test" in r.headers["Tags"].split(",")


def test_test_environment_without_a_test_topic_sends_nothing(wire, monkeypatch):
    calls, mails = wire
    monkeypatch.setattr(settings, "alert_environment", "test-stack")
    assert alert_channels.configured_channels() == []
    assert alert_channels.deliver(Notification(title="t", message="m")) == {}
    assert calls == [] and mails == []


def test_a_test_fire_in_production_is_labelled_and_goes_to_the_test_topic(wire, monkeypatch):
    calls, mails = wire
    monkeypatch.setattr(settings, "alert_test_ntfy_url", TEST_TOPIC)
    out = alert_channels.deliver(Notification(title="hello", message="world", test=True))
    assert out == {"ntfy-test": True}
    assert [str(r.url) for r in calls] == [TEST_TOPIC] and mails == []
    assert calls[0].headers["Title"] == "[TEST] hello"


def test_fire_test_alert_cli(wire, monkeypatch):
    from app.ops import fire_test_alert

    calls, mails = wire
    monkeypatch.setattr(settings, "alert_test_ntfy_url", TEST_TOPIC)
    lines: list[str] = []
    assert fire_test_alert.main(["checking", "the", "pager"], out=lines.append) == 0
    assert json.loads(lines[-1]) == {"channels": ["ntfy-test"], "outcome": {"ntfy-test": True}}
    assert [str(r.url) for r in calls] == [TEST_TOPIC] and mails == []
    assert calls[0].content.decode() == "[TEST] checking the pager"
    # No test topic: nothing is sent at all (the owner's channels are configured).
    monkeypatch.setattr(settings, "alert_test_ntfy_url", "")
    calls.clear()
    assert fire_test_alert.main(["again"], out=lines.append) == 0
    assert calls == [] and mails == []


def test_the_pager_labels_a_test_stack_incident(wire, monkeypatch):
    from app.services.alert_pager import MemoryStore, Pager, Signal

    calls, _ = wire
    monkeypatch.setattr(settings, "alert_environment", "test")
    monkeypatch.setattr(settings, "alert_test_ntfy_url", TEST_TOPIC)
    p = Pager(MemoryStore())
    p.fire(Signal(group="service:backend", source="s", severity="critical", title="Chat is down", body="b"))
    p.clear("service:backend", "s")
    assert [r.headers["Title"] for r in calls] == ["[TEST] Chat is down", "[TEST] Recovered: Chat is down"]
    assert {str(r.url) for r in calls} == {TEST_TOPIC}


def test_another_environment_is_labelled_but_routed_normally(wire, monkeypatch):
    calls, _ = wire
    monkeypatch.setattr(settings, "alert_environment", "staging")
    alert_channels.deliver(Notification(title="t", message="m", severity="warning"))
    ntfy = [r for r in calls if r.url.host == "ntfy.example"]
    assert [str(r.url) for r in ntfy] == [OWNER] and ntfy[0].headers["Title"] == "[STAGING] t"


@pytest.mark.parametrize("env", ["", "production", "live", "PROD"])
def test_the_real_system_is_not_labelled(wire, monkeypatch, env):
    calls, _ = wire
    monkeypatch.setattr(settings, "alert_environment", env)
    alert_channels.deliver(Notification(title="t", message="m"))
    ntfy = [r for r in calls if r.url.host == "ntfy.example"]
    assert ntfy[0].headers["Title"] == "t" and str(ntfy[0].url) == OWNER


def test_the_test_topic_url_is_kept_out_of_the_httpx_log(monkeypatch, caplog):
    import logging

    monkeypatch.setattr(settings, "alert_test_ntfy_url", TEST_TOPIC)
    logger = logging.getLogger("httpx")
    with caplog.at_level(logging.INFO, logger="httpx"):
        logger.info('HTTP Request: POST %s "HTTP/1.1 200 OK"', TEST_TOPIC)
    assert "throwaway-test-topic" not in caplog.text
