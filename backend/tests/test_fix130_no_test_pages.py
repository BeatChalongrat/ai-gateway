"""FIX130 — the unit lane can never page a person.

The lane runs with the live backend container's environment. On 2026-09-23
that environment carried the owner's ALERT_NTFY_URL, and every test that
fired an alert rule POSTed a fake "test-rule: …" notification to the owner's
phone (17 of them), while the two rule tests that expect
``delivery_status == "log_only"`` failed because a channel was configured.

``conftest._alert_channels_off_by_default`` blanks the global channels for
every test. Proven RED/GREEN by running this file with
``-e ALERT_NTFY_URL=http://127.0.0.1:9/fix130-dummy`` (an unreachable
dummy — never a real topic).
"""

from __future__ import annotations

from app.core.config import settings
from app.services import alert_channels


def test_no_alert_channel_is_configured_inside_a_test():
    assert alert_channels.configured_channels() == []
    assert settings.alert_ntfy_url == ""
    assert settings.alert_email_to == ""
    assert settings.alert_webhook_url == ""
    assert settings.alert_ntfy_token == ""


def test_a_test_can_still_opt_in_to_a_fake_endpoint(monkeypatch):
    monkeypatch.setattr(settings, "alert_ntfy_url", "https://ntfy.example/fake")
    assert alert_channels.configured_channels() == ["ntfy"]
