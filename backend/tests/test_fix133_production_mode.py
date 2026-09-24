"""FIX133 B — production mode without dev crutches.

* EMAIL_DELIVERY=disabled is an honest production choice: validate_secrets
  accepts it without a Resend key, and a send reports "not delivered"
  instead of pretending (or silently dropping).
* The API docs are off outside dev/test unless API_DOCS_ENABLED says so.
* The master-key rotation rewrites every blob exactly once and refuses a
  blob it cannot read.
"""

from __future__ import annotations

import logging

import pytest

from app.core.config import Settings
from app.core.security import decrypt_provider_key, encrypt_provider_key


def _prod_settings(**over) -> Settings:
    strong = "x" * 40
    base = dict(
        app_env="production",
        jwt_secret=strong,
        master_encryption_key=strong,
        portal_jwt_secret=strong,
        service_jwt_secret=strong,
        internal_service_secret=strong,
        dlp_audit_key="a" * 64,
        session_signing_secrets=strong,
        provider_key_master_key="b" * 64,
        resend_api_key="",
        auth_cookie_samesite="lax",
    )
    base.update(over)
    return Settings(**base)


def test_production_refuses_a_missing_mail_sender_unless_it_is_stated():
    with pytest.raises(RuntimeError) as ei:
        _prod_settings(email_delivery="auto").validate_secrets()
    assert "RESEND_API_KEY" in str(ei.value)
    _prod_settings(email_delivery="disabled").validate_secrets()  # stated: fine
    _prod_settings(email_delivery="resend", resend_api_key="re_x").validate_secrets()
    with pytest.raises(RuntimeError) as ei:
        _prod_settings(email_delivery="pigeon").validate_secrets()
    assert "EMAIL_DELIVERY" in str(ei.value)


def test_disabled_delivery_reports_not_delivered(monkeypatch, caplog):
    from app.services.email import sender

    monkeypatch.setattr(sender.settings, "email_delivery", "disabled", raising=False)
    monkeypatch.setattr(sender.settings, "app_env", "production", raising=False)
    with caplog.at_level(logging.WARNING):
        res = sender.send_email(to="a@example.com", subject="s", html_body="<p>h</p>", text_body="t")
    assert res.delivered is False and res.provider == "disabled" and res.reason == "email_delivery_disabled"
    assert any("email.delivery_disabled" in r.getMessage() for r in caplog.records)


def test_console_delivery_can_be_forced_outside_dev(monkeypatch):
    from app.services.email import sender

    monkeypatch.setattr(sender.settings, "email_delivery", "console", raising=False)
    monkeypatch.setattr(sender.settings, "app_env", "production", raising=False)
    res = sender.send_email(to="a@example.com", subject="s", html_body="<p>h</p>", text_body="t")
    assert res.delivered is True and res.provider == "console"


def test_api_docs_follow_the_environment():
    assert Settings(app_env="development").api_docs_on is True
    assert Settings(app_env="production").api_docs_on is False
    assert Settings(app_env="production", api_docs_enabled=True).api_docs_on is True
    assert Settings(app_env="development", api_docs_enabled=False).api_docs_on is False


def test_master_key_rotation_rewrites_once_and_refuses_foreign_blobs():
    from scripts.reencrypt_master_key import DEV_KEY, rewrite_value

    new_key = "c" * 64
    blob = encrypt_provider_key("sk-live-1234", DEV_KEY)
    out, state = rewrite_value(blob, DEV_KEY, new_key)
    assert state == "rewritten" and decrypt_provider_key(out, new_key) == "sk-live-1234"
    out2, state2 = rewrite_value(out, DEV_KEY, new_key)
    assert state2 == "already_new" and out2 == out  # idempotent
    assert rewrite_value(None, DEV_KEY, new_key) == (None, "empty")
    foreign = encrypt_provider_key("x", "d" * 64)
    with pytest.raises(Exception):
        rewrite_value(foreign, DEV_KEY, new_key)


def test_empty_optional_booleans_mean_unset(monkeypatch):
    """compose passes ``${AUTH_COOKIE_SECURE:-}`` / ``${API_DOCS_ENABLED:-}`` — an
    empty string is "not set" (auto), not a boot failure (found live)."""
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "")
    monkeypatch.setenv("API_DOCS_ENABLED", "")
    s = Settings(app_env="production")
    assert s.auth_cookie_secure is None and s.api_docs_enabled is None
    assert s.cookie_secure_effective is True and s.api_docs_on is False
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("API_DOCS_ENABLED", "true")
    s = Settings(app_env="production")
    assert s.auth_cookie_secure is False and s.api_docs_on is True
