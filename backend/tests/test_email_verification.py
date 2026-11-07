"""Phase 2C — Commit 14: require verified email to login (TDD).

Contract:

* Portal signup leaves ``email_verified=False`` by default — unchanged.
* Login of an unverified user returns ``403 email_not_verified`` with
  a specific error code so the UI can show a "please check your
  inbox" message rather than a generic "invalid password".
* ``POST /portal/auth/request-verification-email`` accepts
  ``{"email": "..."}`` and mints a short-lived JWT (purpose
  "email_verify") keyed by user id. In dev/test it returns the token
  in the response body so we can drive the next step without an SMTP
  harness; production stubs the body and emails the link.
* ``POST /portal/auth/verify-email`` accepts ``{"token": "<jwt>"}``,
  decodes + verifies it, and flips the user's ``email_verified`` to
  True. A used token cannot be replayed (server re-issues if the
  user re-requests).
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def seeded_user():
    """Create an unverified portal user with a known password."""
    import bcrypt

    from app.models.portal_user import PortalUserORM
    from tests.conftest import _IntegrationSession

    email = f"pv-{uuid.uuid4().hex[:8]}@example.com"
    pw = "LongEnoughPass9!"
    db = _IntegrationSession()
    try:
        u = PortalUserORM(
            id=str(uuid.uuid4()),
            email=email,
            password_hash=bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=4)).decode(),
            display_name="Test",
            credit_balance=0,
            credit_used=0,
            plan="free_trial",
            status="active",
            email_verified=False,
            # tenant_id is NOT NULL after SSO Phase 1 A1 (matches
            # migration 030); SQLite doesn't enforce the FK so a bare
            # id string is enough for this fixture.
            tenant_id="t-fixture",
        )
        db.add(u)
        db.commit()
        uid = u.id
    finally:
        db.close()

    yield {"id": uid, "email": email, "password": pw}

    db = _IntegrationSession()
    try:
        row = db.get(PortalUserORM, uid)
        if row is not None:
            db.delete(row)
            db.commit()
    finally:
        db.close()


class TestUnverifiedLoginBlocked:
    def test_unverified_login_returns_403(self, client, seeded_user):
        r = client.post(
            "/portal/auth/login",
            json={"email": seeded_user["email"], "password": seeded_user["password"]},
        )
        assert r.status_code == 403, r.text
        body = r.json()
        # Specific code so the UI can route to a resend-verification CTA.
        assert body["error"]["code"] == "email_not_verified"

    def test_verified_login_succeeds(self, client, seeded_user):
        # Flip the flag directly and retry.
        from app.models.portal_user import PortalUserORM
        from tests.conftest import _IntegrationSession

        db = _IntegrationSession()
        try:
            u = db.get(PortalUserORM, seeded_user["id"])
            u.email_verified = True
            db.commit()
        finally:
            db.close()

        r = client.post(
            "/portal/auth/login",
            json={"email": seeded_user["email"], "password": seeded_user["password"]},
        )
        assert r.status_code == 200


class TestVerifyEmailEndpoint:
    def test_request_then_verify_flow(self, client, seeded_user):
        r1 = client.post(
            "/portal/auth/request-verification-email",
            json={"email": seeded_user["email"]},
        )
        assert r1.status_code == 200, r1.text
        body = r1.json()
        # Dev/test only: the token is returned directly so the test
        # can proceed without a mailer.
        token = body.get("token")
        assert token, "expected ``token`` in response body for dev/test"

        r2 = client.post(
            "/portal/auth/verify-email",
            json={"token": token},
        )
        assert r2.status_code == 200, r2.text

        # Now login should work.
        r3 = client.post(
            "/portal/auth/login",
            json={"email": seeded_user["email"], "password": seeded_user["password"]},
        )
        assert r3.status_code == 200

    def test_malformed_token_rejected(self, client):
        r = client.post(
            "/portal/auth/verify-email",
            json={"token": "not-a-real-jwt"},
        )
        assert r.status_code in (400, 401, 422)

    def test_wrong_token_purpose_rejected(self, client, seeded_user):
        """A portal access token should NOT verify the email. The
        endpoint must reject any JWT whose ``purpose`` isn't
        email_verify."""
        from app.services.portal import auth_service

        access_tok = auth_service.create_portal_token(
            seeded_user["id"], seeded_user["email"], "access"
        )
        r = client.post(
            "/portal/auth/verify-email",
            json={"token": access_tok},
        )
        assert r.status_code in (400, 401, 422)

    def test_request_for_unknown_email_is_quiet_200(self, client):
        """Don't leak user existence through the response. Return 200
        regardless of whether the email is registered."""
        r = client.post(
            "/portal/auth/request-verification-email",
            json={"email": "ghost-never-signed-up@example.test"},
        )
        assert r.status_code == 200
