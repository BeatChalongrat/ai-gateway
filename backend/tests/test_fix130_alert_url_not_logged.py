"""FIX130 E4 — the ntfy topic (and a webhook's secret path) never reaches a log.

An ntfy.sh topic is the only secret of that channel: anyone who knows the
topic can read every alert. httpx logs each request at INFO as
``HTTP Request: POST https://ntfy.sh/<topic> "HTTP/1.1 200 OK"`` and the
backend's root logger is at INFO, so the first alert the backend delivered
would have written the owner's topic into the backend log (the unit lane
printed it on 2026-09-23). The same holds for a webhook URL whose path is
its credential (Slack-style ``/services/T…/B…/<secret>``), and
``WebhookService`` logged the full rule webhook URL itself.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from app.core.config import settings
from app.services import alert_channels
from app.services.alert_channels import Notification
from app.services.webhook_service import WebhookService

TOPIC = "sran-alerts-9f8e7d6c5b4a3928"
HOOK_SECRET = "B0000/XXXXsecretXXXX"


@pytest.fixture()
def mock_http(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "x"})

    real_client, real_async = httpx.Client, httpx.AsyncClient

    def _client(**kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real_client(**kw)

    def _async_client(**kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real_async(**kw)

    monkeypatch.setattr(alert_channels.httpx, "Client", _client)
    monkeypatch.setattr("app.services.webhook_service.httpx.AsyncClient", _async_client)


def test_ntfy_topic_and_webhook_path_are_not_in_the_httpx_log(mock_http, monkeypatch, caplog):
    monkeypatch.setattr(settings, "alert_ntfy_url", f"https://ntfy.example/{TOPIC}")
    monkeypatch.setattr(settings, "alert_webhook_url", f"https://hooks.example/services/T0000/{HOOK_SECRET}")
    with caplog.at_level(logging.INFO, logger="httpx"):
        out = alert_channels.deliver(Notification(title="t", message="m", severity="warning"))
    assert out == {"ntfy": True, "webhook": True}
    text = caplog.text
    assert "HTTP Request: POST https://ntfy.example/" in text  # the line stays, useful
    assert TOPIC not in text
    assert HOOK_SECRET not in text


@pytest.mark.asyncio
async def test_rule_webhook_url_is_not_logged(mock_http, monkeypatch, caplog):
    events: list[dict] = []

    class _Rec:
        def info(self, event, **kw):
            events.append({"event": event, **kw})

        warning = info

    monkeypatch.setattr("app.services.webhook_service.logger", _Rec())
    url = f"https://hooks.example/services/T0000/{HOOK_SECRET}"
    with caplog.at_level(logging.INFO, logger="httpx"):
        d = await WebhookService().deliver(url, {"event": "alert_fired"})
    assert d.status.value == "delivered"
    assert HOOK_SECRET not in caplog.text
    assert events and all(HOOK_SECRET not in str(e) for e in events)
    assert events[0]["url"].startswith("https://hooks.example/")
