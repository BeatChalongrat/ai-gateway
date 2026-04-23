"""Portal invite-flow redesign (2026-04-24) — shareable-link endpoints.

Covers the singular /portal/team/invite routes layered on top of the
existing tenant_service primitives. Tests:

* GET /portal/team/invite — null vs live-invite body shape
* POST /portal/team/invite — idempotent, force=true rotates
* DELETE /portal/team/invite — soft-revoke, idempotent when nothing live
* POST /portal/team/invite/send-email — delivers (dev-mode console),
  dedupes, rejects invalid addresses, returns per-recipient result
* GET /portal/auth/invite/{code} — public preview returns tenant +
  role + inviter for live code, { valid: false } for bogus/revoked
* Redemption is now multi-use — a second signup with the same code
  does NOT fail as "already used"
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


def _signup_owner(client, email: str = "owner@t.com") -> dict:
    r = client.post(
        "/portal/auth/signup",
        json={"email": email, "password": "Test1234!"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestGetActiveInvite:
    def test_null_when_no_live_invite(self, client):
        s = _signup_owner(client)
        r = client.get("/portal/team/invite", headers=_auth(s["access_token"]))
        assert r.status_code == 200
        assert r.json() == {"invite": None}

    def test_returns_live_invite_after_create(self, client):
        s = _signup_owner(client)
        h = _auth(s["access_token"])
        created = client.post("/portal/team/invite", headers=h, json={}).json()
        assert created["invite"]["code"]
        # invite_url is composed from PORTAL_PUBLIC_URL — frontend uses
        # this field directly so the shape has to stay stable.
        assert created["invite"]["invite_url"].endswith(
            created["invite"]["code"]
        )

        got = client.get("/portal/team/invite", headers=h).json()
        assert got["invite"]["code"] == created["invite"]["code"]


class TestCreateInvite:
    def test_idempotent_without_force(self, client):
        s = _signup_owner(client)
        h = _auth(s["access_token"])
        a = client.post("/portal/team/invite", headers=h, json={}).json()
        b = client.post("/portal/team/invite", headers=h, json={}).json()
        assert a["invite"]["code"] == b["invite"]["code"]

    def test_force_rotates_code(self, client):
        s = _signup_owner(client)
        h = _auth(s["access_token"])
        a = client.post("/portal/team/invite", headers=h, json={}).json()
        b = client.post(
            "/portal/team/invite", headers=h, json={"force": True}
        ).json()
        assert a["invite"]["code"] != b["invite"]["code"]

    def test_one_live_invite_per_tenant(self, client):
        """Force-regenerate must not leave two un-revoked rows behind.

        The partial unique index would block a second row anyway, so
        this is also a DB-integrity smoke test.
        """
        from sqlalchemy import select

        s = _signup_owner(client)
        h = _auth(s["access_token"])
        client.post("/portal/team/invite", headers=h, json={}).json()
        client.post(
            "/portal/team/invite", headers=h, json={"force": True}
        ).json()

        db = _Session()
        try:
            rows = (
                db.execute(
                    select(TenantInviteORM).where(
                        TenantInviteORM.revoked_at.is_(None)
                    )
                )
                .scalars()
                .all()
            )
            assert len(rows) == 1
        finally:
            db.close()


class TestRevokeInvite:
    def test_delete_clears_live_invite(self, client):
        s = _signup_owner(client)
        h = _auth(s["access_token"])
        client.post("/portal/team/invite", headers=h, json={}).json()
        r = client.delete("/portal/team/invite", headers=h)
        assert r.status_code == 200
        got = client.get("/portal/team/invite", headers=h).json()
        assert got["invite"] is None

    def test_delete_idempotent(self, client):
        s = _signup_owner(client)
        h = _auth(s["access_token"])
        # No active invite; DELETE is still ok.
        r = client.delete("/portal/team/invite", headers=h)
        assert r.status_code == 200


class TestOwnerOnlyGates:
    def test_member_cannot_read_invite(self, client):
        owner = _signup_owner(client, "owner@t.com")
        inv = client.post(
            "/portal/team/invite",
            headers=_auth(owner["access_token"]),
            json={},
        ).json()
        member = client.post(
            "/portal/auth/signup",
            json={
                "email": "member@t.com",
                "password": "Test1234!",
                "invite_code": inv["invite"]["code"],
            },
        ).json()
        r = client.get(
            "/portal/team/invite", headers=_auth(member["access_token"])
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "owner_required"


class TestPublicPreview:
    def test_live_code_returns_tenant_context(self, client):
        s = _signup_owner(client, "bob@tx.test")
        inv = client.post(
            "/portal/team/invite",
            headers=_auth(s["access_token"]),
            json={},
        ).json()
        r = client.get(f"/portal/auth/invite/{inv['invite']['code']}")
        assert r.status_code == 200
        body = r.json()
        assert body["valid"] is True
        assert body["role"] == "member"
        assert body["tenant_id"] == s["user"]["tenant_id"]

    def test_bogus_code_is_opaque_false(self, client):
        r = client.get("/portal/auth/invite/not-a-real-code")
        assert r.status_code == 200
        assert r.json() == {"valid": False}

    def test_revoked_code_is_opaque_false(self, client):
        s = _signup_owner(client)
        h = _auth(s["access_token"])
        inv = client.post("/portal/team/invite", headers=h, json={}).json()
        client.delete("/portal/team/invite", headers=h)
        r = client.get(f"/portal/auth/invite/{inv['invite']['code']}")
        assert r.json() == {"valid": False}


class TestMultiUseRedemption:
    def test_same_code_redeems_twice(self, client):
        """Redesign: a live code is reusable until expiry. Pre-redesign
        this test would fail with invite_already_used."""
        owner = _signup_owner(client, "owner@t.com")
        inv = client.post(
            "/portal/team/invite",
            headers=_auth(owner["access_token"]),
            json={},
        ).json()
        code = inv["invite"]["code"]

        a = client.post(
            "/portal/auth/signup",
            json={
                "email": "a@t.com",
                "password": "Test1234!",
                "invite_code": code,
            },
        )
        assert a.status_code == 200

        b = client.post(
            "/portal/auth/signup",
            json={
                "email": "b@t.com",
                "password": "Test1234!",
                "invite_code": code,
            },
        )
        assert b.status_code == 200

    def test_revoked_code_refuses_redemption(self, client):
        owner = _signup_owner(client, "owner@t.com")
        h = _auth(owner["access_token"])
        inv = client.post("/portal/team/invite", headers=h, json={}).json()
        client.delete("/portal/team/invite", headers=h)
        r = client.post(
            "/portal/auth/signup",
            json={
                "email": "late@t.com",
                "password": "Test1234!",
                "invite_code": inv["invite"]["code"],
            },
        )
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "invite_revoked"


class TestSendInviteEmail:
    def test_refuses_when_no_live_invite(self, client):
        s = _signup_owner(client)
        h = _auth(s["access_token"])
        r = client.post(
            "/portal/team/invite/send-email",
            headers=h,
            json={"recipients": ["x@test.com"]},
        )
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "no_active_invite"

    def test_delivers_and_dedupes(self, client):
        s = _signup_owner(client)
        h = _auth(s["access_token"])
        client.post("/portal/team/invite", headers=h, json={}).json()
        r = client.post(
            "/portal/team/invite/send-email",
            headers=h,
            json={
                "recipients": [
                    "x@test.com",
                    "y@test.com",
                    "X@test.com",  # duplicate, case-insensitive
                    "not-an-email",
                ]
            },
        )
        assert r.status_code == 200
        body = r.json()
        # Dev-mode sender always delivers for well-formed addresses.
        assert body["total"] == 4
        assert body["sent"] == 2
        # Per-recipient result is positional so the frontend can keep
        # failed chips red.
        statuses = [r["delivered"] for r in body["results"]]
        assert statuses == [True, True, False, False]

    def test_member_cannot_send(self, client):
        owner = _signup_owner(client, "owner@t.com")
        inv = client.post(
            "/portal/team/invite",
            headers=_auth(owner["access_token"]),
            json={},
        ).json()
        member = client.post(
            "/portal/auth/signup",
            json={
                "email": "member@t.com",
                "password": "Test1234!",
                "invite_code": inv["invite"]["code"],
            },
        ).json()
        r = client.post(
            "/portal/team/invite/send-email",
            headers=_auth(member["access_token"]),
            json={"recipients": ["x@test.com"]},
        )
        assert r.status_code == 403


class TestPublicSettingsExposesResendFlag:
    def test_public_settings_has_resend_configured(self, client):
        r = client.get("/portal/auth/public-settings")
        assert r.status_code == 200
        body = r.json()
        # Settings page / Team page both disable "Send via email" when
        # resend isn't configured — they need this flag on the public
        # endpoint because the chip-input UI renders before auth.
        assert "resend_configured" in body
        assert isinstance(body["resend_configured"], bool)
