"""Phase 3.5 Commit 27 — email sender foundation tests.

Verifies:
- Dev-mode writes to the logger, never hits the network.
- Production mode calls Resend (mocked).
- Retry path fires when Resend raises on the initial attempt.
- Invalid recipient short-circuits with delivered=False.
- Wrappers (verification / password reset / invite) route through
  send_email using the EN template.
"""

from __future__ import annotations

import logging

import pytest

from app.core.config import settings
from app.services.email import sender


@pytest.fixture()
def dev_env(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "development")
    yield


@pytest.fixture()
def prod_env(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "resend_api_key", "re_test_key")
    yield


class TestDevModeConsoleSender:
    def test_dev_mode_logs_and_returns_delivered(self, dev_env, caplog):
        caplog.set_level(logging.INFO, logger="email.sender")
        result = sender.send_email(
            to="alice@example.com",
            subject="Hi",
            html_body="<p>Hi</p>",
            text_body="Hi",
        )
        assert result.delivered is True
        assert result.provider == "console"
        records = [r for r in caplog.records if r.name == "email.sender"]
        assert any("email.dev_console" in r.message for r in records)

    def test_invalid_recipient_shortcircuits(self, dev_env):
        result = sender.send_email(
            to="not-an-email",
            subject="Hi",
            html_body="<p>Hi</p>",
            text_body="Hi",
        )
        assert result.delivered is False
        assert result.reason == "invalid_recipient"


class TestProductionResendPath:
    def test_resend_called_with_from_header_and_payload(
        self, prod_env, monkeypatch
    ):
        captured: dict = {}

        def fake_send(payload):
            captured.update(payload)
            return {"id": "msg_123"}

        import resend as real_resend

        monkeypatch.setattr(
            real_resend.Emails, "send", staticmethod(fake_send)
        )
        monkeypatch.setattr(
            settings, "email_from_address", "no-reply@test.example"
        )
        monkeypatch.setattr(settings, "email_from_name", "Test Sender")

        result = sender.send_email(
            to="bob@example.com",
            subject="Hello",
            html_body="<p>Hello</p>",
            text_body="Hello",
        )
        assert result.delivered is True
        assert result.provider == "resend"
        assert result.message_id == "msg_123"
        assert captured["to"] == ["bob@example.com"]
        assert captured["subject"] == "Hello"
        assert captured["html"] == "<p>Hello</p>"
        assert captured["text"] == "Hello"
        assert "Test Sender" in captured["from"]
        assert "no-reply@test.example" in captured["from"]

    def test_resend_failure_returns_delivered_false(self, prod_env, monkeypatch):
        import resend as real_resend

        def raise_boom(_payload):
            raise RuntimeError("network is down")

        monkeypatch.setattr(
            real_resend.Emails, "send", staticmethod(raise_boom)
        )
        # Prevent retry from actually scheduling a task (we're sync
        # here; send_email's get_running_loop will RuntimeError which
        # the code handles by logging).
        result = sender.send_email(
            to="bob@example.com",
            subject="Hello",
            html_body="<p>Hello</p>",
            text_body="Hello",
        )
        # Key guarantee: Resend outage does NOT raise to the caller.
        assert result.delivered is False
        assert result.provider == "resend"
        assert "send_failed" in (result.reason or "")


class TestRetrySchedule:
    def test_retry_succeeds_on_second_attempt(self, prod_env, monkeypatch):
        """Patch asyncio.sleep to a no-op so the full retry schedule
        runs in <1ms, then fail-once / succeed-after."""
        import asyncio as real_asyncio
        import resend as real_resend

        calls = {"count": 0}

        def flaky_send(_payload):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("transient")
            return {"id": "msg_retry"}

        monkeypatch.setattr(
            real_resend.Emails, "send", staticmethod(flaky_send)
        )
        monkeypatch.setattr(real_asyncio, "sleep", _noop_sleep)

        result = real_asyncio.run(
            sender._retry_in_background(
                to="retry@example.com",
                subject="r",
                html_body="x",
                text_body="x",
            )
        )
        assert result.delivered is True
        assert calls["count"] >= 2

    def test_retry_gives_up_after_exhaustion(self, prod_env, monkeypatch):
        import asyncio as real_asyncio
        import resend as real_resend

        def always_fail(_payload):
            raise RuntimeError("permanent")

        monkeypatch.setattr(
            real_resend.Emails, "send", staticmethod(always_fail)
        )
        monkeypatch.setattr(real_asyncio, "sleep", _noop_sleep)

        result = real_asyncio.run(
            sender._retry_in_background(
                to="dead@example.com",
                subject="r",
                html_body="x",
                text_body="x",
            )
        )
        assert result.delivered is False


async def _noop_sleep(_seconds):
    return None


class TestWrappers:
    def test_verification_wrapper_renders_and_sends(self, dev_env, caplog):
        caplog.set_level(logging.INFO, logger="email.sender")
        result = sender.send_verification_email(
            to="alice@example.com",
            display_name="Alice",
            verification_link="https://portal.example/verify?token=abc",
        )
        assert result.delivered is True
        # The text body (logged via extra) should carry the link.
        text_bodies = [
            getattr(r, "email_text_body", "")
            for r in caplog.records
        ]
        assert any(
            "https://portal.example/verify?token=abc" in body
            for body in text_bodies
        )
        # And the display name.
        assert any("Alice" in body for body in text_bodies)

    def test_password_reset_wrapper_renders_with_minutes(self, dev_env, caplog):
        caplog.set_level(logging.INFO, logger="email.sender")
        sender.send_password_reset_email(
            to="bob@example.com",
            display_name=None,  # triggers "there" fallback
            reset_link="https://portal.example/reset?token=zzz",
            expires_in_minutes=60,
        )
        subjects = [
            getattr(r, "email_subject", "") for r in caplog.records
        ]
        assert any("Reset" in s for s in subjects)
        text_bodies = [
            getattr(r, "email_text_body", "") for r in caplog.records
        ]
        assert any("60 minutes" in body for body in text_bodies)
        assert any("there" in body for body in text_bodies)

    def test_invite_wrapper_renders_code_and_link(self, dev_env, caplog):
        caplog.set_level(logging.INFO, logger="email.sender")
        sender.send_invite_email(
            to="c@example.com",
            inviter_name="Alice",
            tenant_name="Acme",
            invite_link="https://portal.example/signup?invite=xyz",
            invite_code="xyz-abc-123",
        )
        text_bodies = [
            getattr(r, "email_text_body", "") for r in caplog.records
        ]
        joined = "\n".join(text_bodies)
        assert "xyz-abc-123" in joined
        assert "Acme" in joined
        assert "https://portal.example/signup?invite=xyz" in joined


class TestValidateSecretsGate:
    def test_missing_resend_key_blocks_production_startup(self, monkeypatch):
        monkeypatch.setattr(settings, "app_env", "production")
        monkeypatch.setattr(settings, "resend_api_key", "")
        # Other secrets must be valid so only the Resend check trips.
        strong = "a" * 40
        monkeypatch.setattr(settings, "jwt_secret", strong)
        monkeypatch.setattr(settings, "master_encryption_key", strong)
        monkeypatch.setattr(settings, "portal_jwt_secret", strong)
        monkeypatch.setattr(settings, "session_signing_secrets", strong)
        with pytest.raises(RuntimeError, match="RESEND_API_KEY"):
            settings.validate_secrets()

    def test_dev_env_does_not_require_resend_key(self, monkeypatch):
        monkeypatch.setattr(settings, "app_env", "development")
        monkeypatch.setattr(settings, "resend_api_key", "")
        # Should not raise.
        settings.validate_secrets()
