"""AM9 (I): the mail connector — "send as me" — asks the provider for the narrowest scope that sends,
and a person is offered to connect only what this deployment can connect.

With no Google client configured (the state every new customer starts in) the listing says nothing is
available, so the app offers no connect button that would fail at the provider.
"""

from __future__ import annotations

from app.services.portal import connector_oauth_service as svc


def test_the_mail_connector_asks_to_send_and_nothing_more():
    cfg = svc.connector_config("mail")
    assert cfg["scopes"] == "https://www.googleapis.com/auth/gmail.send"
    assert "readonly" not in cfg["scopes"] and "modify" not in cfg["scopes"]
    assert cfg["settings_prefix"] == "connector_oauth_google"     # the customer's own client: configuration


def test_only_configured_connectors_are_offered(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "connector_oauth_google_client_id", "", raising=False)
    monkeypatch.setattr(settings, "connector_oauth_google_client_secret", "", raising=False)
    assert "mail" not in svc.configured_connectors()
    monkeypatch.setattr(settings, "connector_oauth_google_client_id", "id.apps.example", raising=False)
    monkeypatch.setattr(settings, "connector_oauth_google_client_secret", "s", raising=False)
    assert {"mail", "gmail", "gdrive", "gcal"} <= set(svc.configured_connectors())

