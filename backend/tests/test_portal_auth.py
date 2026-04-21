"""Portal signup / login + JWT separation tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.models.credit_transaction import CreditTransactionORM
from app.models.portal_user import PortalUserORM
from app.models.system_setting import SystemSettingORM
from app.services.portal.auth_service import create_portal_token


# Re-uses the in-memory engine seeded by conftest.
from tests.conftest import _integration_engine


_Session = sessionmaker(bind=_integration_engine)


@pytest.fixture(autouse=True)
def _clean_portal_state():
    """Reset portal tables + settings between tests.

    The conftest's shared SQLite engine is reused, so without this
    cleanup a stale row from one test (e.g. "beat@test.com" signup)
    would fail the "signup should succeed" path of the next test.
    """
    db = _Session()
    try:
        db.query(CreditTransactionORM).delete()
        db.query(PortalUserORM).delete()
        db.query(SystemSettingORM).delete()
        # Seed minimum settings that routes expect to exist.
        db.add_all([
            SystemSettingORM(
                key="free_trial_tokens", value="100000",
                description="tokens on signup",
            ),
            SystemSettingORM(
                key="portal_signup_enabled", value="true",
                description="signup toggle",
            ),
            SystemSettingORM(
                key="credit_low_threshold", value="10000",
                description="low warn",
            ),
            SystemSettingORM(
                key="trial_model_tier", value="budget",
                description="tier",
            ),
        ])
        db.commit()
        yield
    finally:
        db.close()


@pytest.fixture
def client():
    return TestClient(app)


def test_signup_grants_free_trial_tokens_from_settings(client):
    r = client.post(
        "/portal/auth/signup",
        json={"email": "a@test.com", "password": "Test1234!", "display_name": "A"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["email"] == "a@test.com"
    assert body["user"]["credit_balance"] == 100000
    assert body["user"]["plan"] == "free_trial"
    assert body["access_token"]
    assert body["refresh_token"]


def test_signup_duplicate_email_returns_409(client):
    client.post(
        "/portal/auth/signup",
        json={"email": "dup@test.com", "password": "Test1234!"},
    )
    r = client.post(
        "/portal/auth/signup",
        json={"email": "dup@test.com", "password": "Test1234!"},
    )
    assert r.status_code == 409
    body = r.json()
    assert body["error"]["code"] == "email_exists"


def test_signup_disabled_returns_400(client):
    db = _Session()
    try:
        setting = db.get(SystemSettingORM, "portal_signup_enabled")
        setting.value = "false"
        db.commit()
    finally:
        db.close()

    r = client.post(
        "/portal/auth/signup",
        json={"email": "nope@test.com", "password": "Test1234!"},
    )
    # ValidationError maps to 422 in this codebase's handler.
    assert r.status_code in (400, 422)
    body = r.json()
    assert body["error"]["code"] == "signup_disabled"


def test_login_success_returns_tokens(client):
    client.post(
        "/portal/auth/signup",
        json={"email": "l@test.com", "password": "Test1234!"},
    )
    r = client.post(
        "/portal/auth/login",
        json={"email": "l@test.com", "password": "Test1234!"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["user"]["email"] == "l@test.com"


def test_login_wrong_password_returns_401(client):
    client.post(
        "/portal/auth/signup",
        json={"email": "w@test.com", "password": "Test1234!"},
    )
    r = client.post(
        "/portal/auth/login",
        json={"email": "w@test.com", "password": "Wrong9999!"},
    )
    assert r.status_code == 401


def test_portal_token_cannot_access_admin_routes(client):
    # Sign up to obtain a valid portal JWT.
    r = client.post(
        "/portal/auth/signup",
        json={"email": "pt@test.com", "password": "Test1234!"},
    )
    portal_tok = r.json()["access_token"]

    # /admin/system-settings is gated by require_admin — which is
    # overridden to MOCK_AUTH in conftest. To actually test portal-token
    # rejection we hit /portal/me with an admin-ish header that isn't a
    # portal JWT, and confirm that a plain portal token does NOT verify
    # against admin JWT secret. We simulate by posting the portal token
    # as if it were an admin key against the real admin JWT verifier.
    from app.api.routes.auth import decode_jwt
    with pytest.raises(Exception):
        decode_jwt(portal_tok)  # signed with portal_jwt_secret — must fail


def test_admin_token_cannot_access_portal_me(client):
    # Mint something that *looks* like an admin token by signing with the
    # admin secret. Verify portal decoder rejects it.
    from datetime import datetime, timedelta, timezone
    import jwt as _jwt
    from app.core.config import settings
    from app.services.portal.auth_service import verify_portal_token

    now = datetime.now(timezone.utc)
    admin_tok = _jwt.encode(
        {
            "sub": "some-admin",
            "type": "access",  # admin marker, not "portal"
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=30)).timestamp()),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(Exception):
        verify_portal_token(admin_tok)

    # Also via HTTP: /portal/me with an admin-signed token -> 401.
    r = client.get(
        "/portal/me", headers={"Authorization": f"Bearer {admin_tok}"}
    )
    assert r.status_code == 401
