"""Phase 3 Commit 22 — portal team routes (members + invites + redeem).

Covers:
- GET /portal/team/members — all members see the list.
- POST /portal/team/invites — owner-only, code returned inline.
- GET /portal/team/invites — owner-only, active invites with codes.
- DELETE /portal/team/invites/{id} — owner-only, revokes.
- DELETE /portal/team/members/{id} — owner removes anyone, member
  self-removes, non-owner → 403 removing someone else.
- POST /portal/team/redeem — authenticated user joins tenant.

Cross-tenant probing: an owner in tenant A cannot revoke tenant B's
invite, cannot remove tenant B's member.
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
                SystemSettingORM(
                    key="free_trial_tokens", value="100000"
                ),
                SystemSettingORM(
                    key="portal_signup_enabled", value="true"
                ),
            ]
        )
        db.commit()
        yield
    finally:
        db.close()


@pytest.fixture
def client():
    return TestClient(app)


def _signup(client, email: str, **kwargs) -> dict:
    r = client.post(
        "/portal/auth/signup",
        json={"email": email, "password": "Test1234!", **kwargs},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # HttpOnly migration: tokens are cookies. Capture, then clear the
    # shared jar so multi-user tests auth via the explicit Bearer header.
    body["access_token"] = r.cookies.get("portal_access_token")
    body["refresh_token"] = r.cookies.get("portal_refresh_token")
    client.cookies.clear()
    return body


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestListMembers:
    def test_owner_sees_all_members(self, client):
        owner = _signup(client, "owner@t.com")
        # Invite + redeem a member.
        inv = client.post(
            "/portal/team/invites",
            headers=_auth(owner["access_token"]),
            json={},
        ).json()
        _signup(client, "m@t.com", invite_code=inv["code"])

        r = client.get(
            "/portal/team/members", headers=_auth(owner["access_token"])
        )
        assert r.status_code == 200
        emails = {m["email"] for m in r.json()["members"]}
        assert emails == {"owner@t.com", "m@t.com"}

    def test_member_sees_same_list_as_owner(self, client):
        owner = _signup(client, "owner@t.com")
        inv = client.post(
            "/portal/team/invites",
            headers=_auth(owner["access_token"]),
            json={},
        ).json()
        member = _signup(client, "m@t.com", invite_code=inv["code"])
        r = client.get(
            "/portal/team/members",
            headers=_auth(member["access_token"]),
        )
        assert r.status_code == 200
        assert len(r.json()["members"]) == 2


class TestCreateInvite:
    def test_owner_can_create_invite(self, client):
        owner = _signup(client, "owner@t.com")
        r = client.post(
            "/portal/team/invites",
            headers=_auth(owner["access_token"]),
            json={"email_hint": "bob@example.com"},
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body["code"]) >= 43
        assert body["email_hint"] == "bob@example.com"

    def test_member_cannot_create_invite(self, client):
        owner = _signup(client, "owner@t.com")
        inv = client.post(
            "/portal/team/invites",
            headers=_auth(owner["access_token"]),
            json={},
        ).json()
        member = _signup(client, "m@t.com", invite_code=inv["code"])
        r = client.post(
            "/portal/team/invites",
            headers=_auth(member["access_token"]),
            json={},
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "owner_required"


class TestListInvites:
    def test_owner_sees_own_invites(self, client):
        owner = _signup(client, "owner@t.com")
        # Two invites.
        c1 = client.post(
            "/portal/team/invites",
            headers=_auth(owner["access_token"]),
            json={},
        ).json()["code"]
        c2 = client.post(
            "/portal/team/invites",
            headers=_auth(owner["access_token"]),
            json={},
        ).json()["code"]
        r = client.get(
            "/portal/team/invites",
            headers=_auth(owner["access_token"]),
        )
        codes = {inv["code"] for inv in r.json()["invites"]}
        assert codes == {c1, c2}

    def test_used_invites_do_not_appear(self, client):
        owner = _signup(client, "owner@t.com")
        used_code = client.post(
            "/portal/team/invites",
            headers=_auth(owner["access_token"]),
            json={},
        ).json()["code"]
        _signup(client, "m@t.com", invite_code=used_code)
        r = client.get(
            "/portal/team/invites",
            headers=_auth(owner["access_token"]),
        )
        assert r.json()["invites"] == []


class TestRevokeInvite:
    def test_owner_revokes_pending_invite(self, client):
        owner = _signup(client, "owner@t.com")
        inv = client.post(
            "/portal/team/invites",
            headers=_auth(owner["access_token"]),
            json={},
        ).json()
        r = client.delete(
            f"/portal/team/invites/{inv['id']}",
            headers=_auth(owner["access_token"]),
        )
        assert r.status_code == 200
        # Revoked invite can no longer be redeemed.
        signup_attempt = client.post(
            "/portal/auth/signup",
            json={
                "email": "too-late@t.com",
                "password": "Test1234!",
                "invite_code": inv["code"],
            },
        )
        assert signup_attempt.status_code == 409
        # Post-invite-redesign (2026-04-24): revoked codes fail with
        # ``invite_revoked``. Pre-redesign the code was
        # ``invite_already_used`` because revoke and redeem both wrote
        # to ``used_at``. Splitting the columns split the error codes.
        assert signup_attempt.json()["error"]["code"] == "invite_revoked"

    def test_cross_tenant_revoke_returns_404(self, client):
        owner_a = _signup(client, "a@t.com")
        inv_a = client.post(
            "/portal/team/invites",
            headers=_auth(owner_a["access_token"]),
            json={},
        ).json()
        owner_b = _signup(client, "b@t.com")
        r = client.delete(
            f"/portal/team/invites/{inv_a['id']}",
            headers=_auth(owner_b["access_token"]),
        )
        assert r.status_code == 404


class TestRemoveMember:
    def test_owner_removes_member(self, client):
        owner = _signup(client, "owner@t.com")
        inv = client.post(
            "/portal/team/invites",
            headers=_auth(owner["access_token"]),
            json={},
        ).json()
        member = _signup(client, "m@t.com", invite_code=inv["code"])
        r = client.delete(
            f"/portal/team/members/{member['user']['id']}",
            headers=_auth(owner["access_token"]),
        )
        assert r.status_code == 200
        # Member is now orphaned — /portal/team/members on the owner
        # side only shows the owner.
        list_r = client.get(
            "/portal/team/members",
            headers=_auth(owner["access_token"]),
        )
        emails = {m["email"] for m in list_r.json()["members"]}
        assert emails == {"owner@t.com"}

    def test_member_cannot_remove_another_member(self, client):
        owner = _signup(client, "owner@t.com")
        i1 = client.post(
            "/portal/team/invites",
            headers=_auth(owner["access_token"]),
            json={},
        ).json()
        m1 = _signup(client, "m1@t.com", invite_code=i1["code"])
        i2 = client.post(
            "/portal/team/invites",
            headers=_auth(owner["access_token"]),
            json={},
        ).json()
        m2 = _signup(client, "m2@t.com", invite_code=i2["code"])
        r = client.delete(
            f"/portal/team/members/{m2['user']['id']}",
            headers=_auth(m1["access_token"]),
        )
        assert r.status_code == 403

    def test_member_can_self_remove(self, client):
        owner = _signup(client, "owner@t.com")
        inv = client.post(
            "/portal/team/invites",
            headers=_auth(owner["access_token"]),
            json={},
        ).json()
        member = _signup(client, "m@t.com", invite_code=inv["code"])
        r = client.delete(
            f"/portal/team/members/{member['user']['id']}",
            headers=_auth(member["access_token"]),
        )
        assert r.status_code == 200

    def test_last_owner_cannot_be_removed(self, client):
        owner = _signup(client, "solo@t.com")
        r = client.delete(
            f"/portal/team/members/{owner['user']['id']}",
            headers=_auth(owner["access_token"]),
        )
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "last_owner_remaining"


class TestRedeemEndpoint:
    def test_authenticated_user_with_no_tenant_can_redeem(self, client):
        """After being removed from a tenant, a user is displaced into a
        fresh solo tenant (tenant_id is NOT NULL since SSO Phase 1) —
        as its sole member they can use /portal/team/redeem to fold it
        into a new team, so the removed→re-invited flow still works."""
        owner = _signup(client, "owner@t.com")
        inv1 = client.post(
            "/portal/team/invites",
            headers=_auth(owner["access_token"]),
            json={},
        ).json()
        member = _signup(client, "m@t.com", invite_code=inv1["code"])
        # Owner removes the member, who is now tenant-less.
        client.delete(
            f"/portal/team/members/{member['user']['id']}",
            headers=_auth(owner["access_token"]),
        )
        # Owner mints a second invite.
        inv2 = client.post(
            "/portal/team/invites",
            headers=_auth(owner["access_token"]),
            json={},
        ).json()
        # Orphaned member's old token has a stale token_version now
        # (bumped by the removal). Log in fresh.
        fresh_r = client.post(
            "/portal/auth/login",
            json={"email": "m@t.com", "password": "Test1234!"},
        )
        fresh = fresh_r.json()
        # HttpOnly migration: token is a cookie now.
        fresh["access_token"] = fresh_r.cookies.get("portal_access_token")
        client.cookies.clear()
        r = client.post(
            "/portal/team/redeem",
            headers=_auth(fresh["access_token"]),
            json={"code": inv2["code"]},
        )
        assert r.status_code == 200
        assert r.json()["tenant_role"] == "member"
