"""FIX129 A4 — alert delivery to a channel a person actually sees (ntfy)."""

from __future__ import annotations

import httpx
import pytest

from app.core.config import settings
from app.services import alert_channels
from app.services.alert_channels import Notification


@pytest.fixture()
def captured(monkeypatch):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.host == "broken.example":
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={"id": "x"})

    real_client = httpx.Client

    def _client(**kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real_client(**kw)

    monkeypatch.setattr(alert_channels.httpx, "Client", _client)
    monkeypatch.setattr(settings, "alert_ntfy_url", "")
    monkeypatch.setattr(settings, "alert_ntfy_token", "")
    monkeypatch.setattr(settings, "alert_email_to", "")
    monkeypatch.setattr(settings, "alert_webhook_url", "")
    return calls


def test_nothing_configured_sends_nothing(captured):
    assert alert_channels.configured_channels() == []
    assert alert_channels.deliver(Notification(title="t", message="m")) == {}
    assert captured == []


def test_ntfy_gets_title_priority_and_tags(captured, monkeypatch):
    monkeypatch.setattr(settings, "alert_ntfy_url", "https://ntfy.example/sran-alerts")
    out = alert_channels.deliver(
        Notification(title="backend is unhealthy", message="ai-gateway-backend-1: Up 2 h (unhealthy)",
                     severity="critical", source="ops-watch", tags=("ops",))
    )
    assert out == {"ntfy": True}
    req = captured[0]
    assert str(req.url) == "https://ntfy.example/sran-alerts"
    assert req.headers["Title"] == "backend is unhealthy"
    assert req.headers["Priority"] == "5"
    assert req.headers["Tags"].startswith("rotating_light,critical")
    assert req.content == b"ai-gateway-backend-1: Up 2 h (unhealthy)"
    assert "Authorization" not in req.headers


def test_failed_channel_reports_false_without_raising(captured, monkeypatch):
    monkeypatch.setattr(settings, "alert_ntfy_url", "https://broken.example/t")
    monkeypatch.setattr(settings, "alert_webhook_url", "https://hook.example/x")
    out = alert_channels.deliver(Notification(title="t", message="m", severity="warning"))
    assert out == {"ntfy": False, "webhook": True}
    hook = [r for r in captured if r.url.host == "hook.example"][0]
    assert hook.headers["content-type"].startswith("application/json")
    assert b'"severity": "warning"' in hook.content or b'"severity":"warning"' in hook.content


def test_thai_title_survives_the_header(captured, monkeypatch):
    monkeypatch.setattr(settings, "alert_ntfy_url", "https://ntfy.example/t")
    out = alert_channels.deliver(Notification(title="ระบบล่ม", message="ข้อความ", severity="critical"))
    assert out == {"ntfy": True}
    assert captured[0].content.decode("utf-8") == "ข้อความ"
    assert captured[0].headers["Title"].startswith("=?UTF-8?B?")  # RFC 2047, ntfy decodes it
