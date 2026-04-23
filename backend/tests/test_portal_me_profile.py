"""/portal/me GET + PATCH regression tests for the portal-UX overhaul.

The Settings page depends on:
  * GET /portal/me returning ``preferred_language`` and ``email_verified``
    so the Preferences language toggle can preselect the right option
    without an extra round-trip.
  * PATCH /portal/me accepting ``preferred_language`` (en/th) and echoing
    the updated value back.
  * Team page depends on GET /portal/team/members including ``avatar_url``
    so the roster can render the same circle the user sees in the
    sidebar.

These are tiny and cheap; if they fail, the Settings language toggle
silently stops saving or the Team page can't render avatars.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.models.api_key import ApiKeyORM
from app.models.credit_transaction import CreditTransactionORM
from app.models.portal_user import PortalUserORM
from app.models.project import ProjectORM
from app.models.system_setting import SystemSettingORM
from app.models.tenant import TenantORM
from app.models.tenant_invite import TenantInviteORM
from tests.conftest import _integration_engine


_Session = sessionmaker(bind=_integration_engine)


@pytest.fixture(autouse=True)
def _clean():
    db = _Session()
    try:
        db.query(TenantInviteORM).delete(synchronize_session=False)
        db.query(ApiKeyORM).filter(
            ApiKeyORM.portal_user_id.isnot(None)
        ).delete(synchronize_session=False)
        db.query(ProjectORM).filter(
            ProjectORM.portal_user_id.isnot(None)
        ).delete(synchronize_session=False)
        db.query(CreditTransactionORM).delete(synchronize_session=False)
        db.query(PortalUserORM).delete(synchronize_session=False)
        db.query(TenantORM).filter(
            ~TenantORM.tenant_id.in_(["test-tenant"])
        ).delete(synchronize_session=False)
        db.query(SystemSettingORM).delete(synchronize_session=False)
        db.add_all(
            [
                SystemSettingORM(key="free_trial_tokens", value="100000"),
                SystemSettingORM(key="portal_signup_enabled", value="true"),
            ]
        )
        db.commit()
        yield
    finally:
        db.close()


@pytest.fixture
def client():
    return TestClient(app)


def _signup(client, email: str = "user@test.com") -> dict:
    r = client.post(
        "/portal/auth/signup",
        json={"email": email, "password": "Test1234!"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestPortalMeResponseShape:
    def test_get_me_exposes_email_verified_and_preferred_language(self, client):
        s = _signup(client)
        r = client.get("/portal/me", headers=_auth(s["access_token"]))
        assert r.status_code == 200
        body = r.json()
        # Settings page reads both fields on load.
        assert "email_verified" in body
        assert "preferred_language" in body
        # Default locale for a fresh account is English — UI falls back
        # to 'en' for unknown anyway, but the default shouldn't drift
        # without a conscious change.
        assert body["preferred_language"] == "en"


class TestPortalMePreferredLanguage:
    def test_patch_sets_preferred_language(self, client):
        s = _signup(client)
        headers = _auth(s["access_token"])

        r = client.patch(
            "/portal/me",
            headers=headers,
            json={"preferred_language": "th"},
        )
        assert r.status_code == 200
        assert r.json()["preferred_language"] == "th"

        # Persists across a fresh GET (not just echoed from the PATCH).
        r2 = client.get("/portal/me", headers=headers)
        assert r2.json()["preferred_language"] == "th"

    def test_patch_language_normalises_case(self, client):
        s = _signup(client)
        r = client.patch(
            "/portal/me",
            headers=_auth(s["access_token"]),
            json={"preferred_language": "EN"},
        )
        assert r.status_code == 200
        # Backend stores lowercase so email-template lookup matches.
        assert r.json()["preferred_language"] == "en"

    def test_patch_rejects_overlong_language_code(self, client):
        s = _signup(client)
        # pydantic Field(max_length=8) — longer values are a 422.
        r = client.patch(
            "/portal/me",
            headers=_auth(s["access_token"]),
            json={"preferred_language": "x" * 32},
        )
        assert r.status_code in (400, 422)


class TestTeamMembersIncludesAvatarUrl:
    def test_members_response_shape_includes_avatar_url(self, client):
        owner = _signup(client, "owner@t.com")
        r = client.get(
            "/portal/team/members", headers=_auth(owner["access_token"])
        )
        assert r.status_code == 200
        members = r.json()["members"]
        assert len(members) >= 1
        # avatar_url is nullable, but the key has to exist — the Team
        # page switches on its value to render the uploaded image vs
        # the deterministic initial circle.
        assert "avatar_url" in members[0]
