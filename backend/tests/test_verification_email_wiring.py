"""Phase 3.5 Commit 29 — verify that request_verification_email
actually dispatches through the Resend sender (or its dev-mode
console stub), not the Phase 2C logger-only stub.

Pairs with test_email_verification.py: that suite asserts the
request/verify round-trip and response shape; this one asserts the
Phase 3.5 wiring: a verification call results in a
``send_verification_email`` invocation with the minted token in the
URL.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models.portal_user import PortalUserORM
from app.services.portal import auth_service
from tests.conftest import _integration_engine


_Session = sessionmaker(bind=_integration_engine)


@pytest.fixture(autouse=True)
def _clean():
    db = _Session()
    try:
        db.query(PortalUserORM).delete(synchronize_session=False)
        db.commit()
        yield
    finally:
        db.close()


class TestVerificationEmailDispatched:
    def test_request_fires_email_sender_with_link_carrying_token(
        self, monkeypatch
    ):
        """The production wiring path: request_verification_email
        calls send_verification_email. We patch the sender to capture
        the call and assert the link is correctly built."""
        captured = {}

        def fake_send(**kwargs):
            captured.update(kwargs)
            from app.services.email.sender import EmailSendResult

            return EmailSendResult(delivered=True, provider="fake")

        monkeypatch.setattr(
            "app.services.email.send_verification_email",
            fake_send,
        )

        db = _Session()
        try:
            user = PortalUserORM(
                id="u-wire-1",
                email="wire@example.com",
                password_hash="hash",
                plan="free_trial",
                status="active",
                email_verified=False,
                credit_balance=0,
                credit_used=0,
                preferred_language="en",
            )
            db.add(user)
            db.commit()
            result = auth_service.request_verification_email(
                db, email="wire@example.com"
            )
        finally:
            db.close()

        assert result["ok"] is True
        assert captured["to"] == "wire@example.com"
        # The link must carry the token we returned from dev-mode.
        assert captured["verification_link"].startswith(
            settings.portal_public_url.rstrip("/")
        )
        assert "token=" in captured["verification_link"]
        # Language plumbed through from user row.
        assert captured["language"] == "en"

    def test_unknown_email_does_not_fire_sender(self, monkeypatch):
        """User-existence guard: unknown email → constant ok response,
        no email sent so the sender is not a probe vector."""
        calls = {"n": 0}

        def counting_send(**_kwargs):
            calls["n"] += 1
            from app.services.email.sender import EmailSendResult

            return EmailSendResult(delivered=True, provider="fake")

        monkeypatch.setattr(
            "app.services.email.send_verification_email",
            counting_send,
        )

        db = _Session()
        try:
            result = auth_service.request_verification_email(
                db, email="nobody@example.com"
            )
        finally:
            db.close()

        assert result == {"ok": True}
        assert calls["n"] == 0

    def test_already_verified_user_does_not_fire_sender(self, monkeypatch):
        calls = {"n": 0}

        def counting_send(**_kwargs):
            calls["n"] += 1
            from app.services.email.sender import EmailSendResult

            return EmailSendResult(delivered=True, provider="fake")

        monkeypatch.setattr(
            "app.services.email.send_verification_email",
            counting_send,
        )

        db = _Session()
        try:
            user = PortalUserORM(
                id="u-verified",
                email="done@example.com",
                password_hash="hash",
                plan="free_trial",
                status="active",
                email_verified=True,
                credit_balance=0,
                credit_used=0,
                preferred_language="en",
            )
            db.add(user)
            db.commit()
            auth_service.request_verification_email(db, email="done@example.com")
        finally:
            db.close()

        assert calls["n"] == 0

    def test_sender_failure_does_not_block_response(self, monkeypatch):
        """Fail-open on Resend outage: the caller still gets
        {'ok': True} and the dev-mode token in the payload."""

        def failing_send(**_kwargs):
            from app.services.email.sender import EmailSendResult

            return EmailSendResult(
                delivered=False, provider="resend", reason="network_down"
            )

        monkeypatch.setattr(
            "app.services.email.send_verification_email",
            failing_send,
        )

        db = _Session()
        try:
            user = PortalUserORM(
                id="u-fail-send",
                email="fail@example.com",
                password_hash="hash",
                plan="free_trial",
                status="active",
                email_verified=False,
                credit_balance=0,
                credit_used=0,
                preferred_language="en",
            )
            db.add(user)
            db.commit()
            result = auth_service.request_verification_email(
                db, email="fail@example.com"
            )
        finally:
            db.close()

        assert result["ok"] is True
        # Dev/test mode exposes the token so the flow can still be
        # exercised in tests even when Resend is down.
        if settings.app_env.lower() in ("development", "test", "testing"):
            assert "token" in result
