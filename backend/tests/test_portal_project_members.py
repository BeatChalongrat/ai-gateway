"""Portal project-member routes (2026-04-24).

Covers the hybrid scope model + project-member CRUD:
  * Tenant Owner sees every project (no explicit membership needed)
  * Tenant Member sees only projects they're attached to
  * GET /portal/projects/{id} returns 403 for non-member non-owner
  * POST /portal/projects auto-grants creator admin + owner admin
  * POST / PATCH / DELETE members gated by project-admin (owner bypass)
  * Last-admin guard blocks demoting / removing the only admin
  * Self-leave path: a member can DELETE their own row without admin
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
from app.models.project_member import ProjectMemberORM
from app.models.system_setting import SystemSettingORM
from app.models.tenant import TenantORM
from app.models.tenant_invite import TenantInviteORM
from tests.conftest import _integration_engine


_Session = sessionmaker(bind=_integration_engine)


@pytest.fixture(autouse=True)
def _clean():
    db = _Session()
    try:
        db.query(ProjectMemberORM).delete(synchronize_session=False)
        db.query(ApiKeyORM).filter(
            ApiKeyORM.portal_user_id.isnot(None)
        ).delete(synchronize_session=False)
        db.query(ProjectORM).filter(
            ProjectORM.portal_user_id.isnot(None)
        ).delete(synchronize_session=False)
        db.query(TenantInviteORM).delete(synchronize_session=False)
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


def _signup(client, email: str, invite: str | None = None) -> dict:
    body = {"email": email, "password": "Test1234!"}
    if invite:
        body["invite_code"] = invite
    r = client.post("/portal/auth/signup", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    # HttpOnly migration: tokens are cookies. Capture, then clear the
    # shared jar so multi-user tests auth via the explicit Bearer header.
    out["access_token"] = r.cookies.get("portal_access_token")
    out["refresh_token"] = r.cookies.get("portal_refresh_token")
    client.cookies.clear()
    return out


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create_project(client, owner_token: str, name: str) -> str:
    r = client.post(
        "/portal/projects",
        headers=_auth(owner_token),
        json={"name": name},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _invite_code(client, owner_token: str) -> str:
    r = client.post(
        "/portal/team/invite", headers=_auth(owner_token), json={}
    )
    assert r.status_code == 200, r.text
    return r.json()["invite"]["code"]


# ---------------------------------------------------------------------------
# List + access scopes
# ---------------------------------------------------------------------------


class TestScopes:
    def test_owner_sees_all_projects(self, client):
        owner = _signup(client, "owner@t.com")
        _create_project(client, owner["access_token"], "p1")
        _create_project(client, owner["access_token"], "p2")
        r = client.get("/portal/projects", headers=_auth(owner["access_token"]))
        assert len(r.json()["projects"]) == 2

    def test_member_sees_only_attached_projects(self, client):
        owner = _signup(client, "owner@t.com")
        code = _invite_code(client, owner["access_token"])
        member = _signup(client, "member@t.com", invite=code)

        p1 = _create_project(client, owner["access_token"], "p1")
        _create_project(client, owner["access_token"], "p2")

        # Member hasn't been attached to any project yet → empty.
        r = client.get(
            "/portal/projects", headers=_auth(member["access_token"])
        )
        assert r.json()["projects"] == []

        # After owner adds them to p1, that one appears.
        r = client.post(
            f"/portal/projects/{p1}/members",
            headers=_auth(owner["access_token"]),
            json={"user_id": member["user"]["id"], "role": "member"},
        )
        assert r.status_code == 200, r.text
        r = client.get(
            "/portal/projects", headers=_auth(member["access_token"])
        )
        names = [p["name"] for p in r.json()["projects"]]
        assert names == ["p1"]

    def test_member_get_unattached_project_is_403(self, client):
        owner = _signup(client, "owner@t.com")
        code = _invite_code(client, owner["access_token"])
        member = _signup(client, "member@t.com", invite=code)
        p = _create_project(client, owner["access_token"], "p")
        r = client.get(
            f"/portal/projects/{p}", headers=_auth(member["access_token"])
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "project_access_denied"

    def test_cross_tenant_get_is_403_not_404(self, client):
        """Tenant isolation: an Acme user asking about a Globex
        project gets the same opaque denial as an Acme-member who
        isn't on the project. Never 404 — that would leak existence."""
        owner_a = _signup(client, "a@t.com")
        owner_b = _signup(client, "b@t.com")
        proj_a = _create_project(client, owner_a["access_token"], "a-proj")
        r = client.get(
            f"/portal/projects/{proj_a}",
            headers=_auth(owner_b["access_token"]),
        )
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# Auto-grants on creation
# ---------------------------------------------------------------------------


class TestAutoGrants:
    def test_owner_create_makes_owner_admin(self, client):
        owner = _signup(client, "owner@t.com")
        p = _create_project(client, owner["access_token"], "p")
        r = client.get(
            f"/portal/projects/{p}/members",
            headers=_auth(owner["access_token"]),
        )
        members = r.json()["members"]
        assert len(members) == 1
        assert members[0]["email"] == "owner@t.com"
        assert members[0]["role"] == "admin"

    def test_member_create_pins_tenant_owner_too(self, client):
        """When a non-owner creates a project, the tenant owner is
        auto-added as admin so the "no project without an admin"
        invariant holds even as the creator leaves the tenant."""
        owner = _signup(client, "owner@t.com")
        code = _invite_code(client, owner["access_token"])
        member = _signup(client, "member@t.com", invite=code)

        # Promote member to admin of a separate project so they can
        # even create — actually, plain tenant-members can create via
        # POST /portal/projects (no gate there). Just create.
        p = _create_project(client, member["access_token"], "m-proj")
        r = client.get(
            f"/portal/projects/{p}/members",
            headers=_auth(owner["access_token"]),
        )
        roles = {m["email"]: m["role"] for m in r.json()["members"]}
        assert roles == {
            "member@t.com": "admin",
            "owner@t.com": "admin",
        }


# ---------------------------------------------------------------------------
# Member CRUD
# ---------------------------------------------------------------------------


class TestMemberCrud:
    def test_admin_adds_tenant_mate(self, client):
        owner = _signup(client, "owner@t.com")
        code = _invite_code(client, owner["access_token"])
        mate = _signup(client, "mate@t.com", invite=code)
        p = _create_project(client, owner["access_token"], "p")

        r = client.post(
            f"/portal/projects/{p}/members",
            headers=_auth(owner["access_token"]),
            json={"user_id": mate["user"]["id"], "role": "viewer"},
        )
        assert r.status_code == 200
        assert r.json()["role"] == "viewer"

    def test_viewer_cannot_add_members(self, client):
        owner = _signup(client, "owner@t.com")
        code = _invite_code(client, owner["access_token"])
        viewer = _signup(client, "viewer@t.com", invite=code)
        mate = _signup(client, "mate@t.com", invite=code)
        p = _create_project(client, owner["access_token"], "p")
        client.post(
            f"/portal/projects/{p}/members",
            headers=_auth(owner["access_token"]),
            json={"user_id": viewer["user"]["id"], "role": "viewer"},
        )
        r = client.post(
            f"/portal/projects/{p}/members",
            headers=_auth(viewer["access_token"]),
            json={"user_id": mate["user"]["id"], "role": "member"},
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "project_admin_required"

    def test_cross_tenant_add_rejected(self, client):
        owner_a = _signup(client, "a@t.com")
        owner_b = _signup(client, "b@t.com")
        p = _create_project(client, owner_a["access_token"], "p")
        r = client.post(
            f"/portal/projects/{p}/members",
            headers=_auth(owner_a["access_token"]),
            json={"user_id": owner_b["user"]["id"], "role": "member"},
        )
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "cross_tenant_member_add"

    def test_duplicate_add_is_409(self, client):
        owner = _signup(client, "owner@t.com")
        code = _invite_code(client, owner["access_token"])
        mate = _signup(client, "mate@t.com", invite=code)
        p = _create_project(client, owner["access_token"], "p")
        client.post(
            f"/portal/projects/{p}/members",
            headers=_auth(owner["access_token"]),
            json={"user_id": mate["user"]["id"], "role": "member"},
        )
        r = client.post(
            f"/portal/projects/{p}/members",
            headers=_auth(owner["access_token"]),
            json={"user_id": mate["user"]["id"], "role": "member"},
        )
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "member_exists"

    def test_patch_role(self, client):
        owner = _signup(client, "owner@t.com")
        code = _invite_code(client, owner["access_token"])
        mate = _signup(client, "mate@t.com", invite=code)
        p = _create_project(client, owner["access_token"], "p")
        client.post(
            f"/portal/projects/{p}/members",
            headers=_auth(owner["access_token"]),
            json={"user_id": mate["user"]["id"], "role": "viewer"},
        )
        r = client.patch(
            f"/portal/projects/{p}/members/{mate['user']['id']}",
            headers=_auth(owner["access_token"]),
            json={"role": "admin"},
        )
        assert r.status_code == 200
        assert r.json()["role"] == "admin"

    def test_delete_removes_member(self, client):
        owner = _signup(client, "owner@t.com")
        code = _invite_code(client, owner["access_token"])
        mate = _signup(client, "mate@t.com", invite=code)
        p = _create_project(client, owner["access_token"], "p")
        client.post(
            f"/portal/projects/{p}/members",
            headers=_auth(owner["access_token"]),
            json={"user_id": mate["user"]["id"], "role": "member"},
        )
        r = client.delete(
            f"/portal/projects/{p}/members/{mate['user']['id']}",
            headers=_auth(owner["access_token"]),
        )
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------


class TestLastAdminGuard:
    def test_cannot_demote_last_admin(self, client):
        owner = _signup(client, "owner@t.com")
        p = _create_project(client, owner["access_token"], "p")
        # The owner is the sole admin on this project.
        r = client.patch(
            f"/portal/projects/{p}/members/{owner['user']['id']}",
            headers=_auth(owner["access_token"]),
            json={"role": "viewer"},
        )
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "last_admin_remaining"

    def test_cannot_remove_last_admin(self, client):
        owner = _signup(client, "owner@t.com")
        p = _create_project(client, owner["access_token"], "p")
        r = client.delete(
            f"/portal/projects/{p}/members/{owner['user']['id']}",
            headers=_auth(owner["access_token"]),
        )
        assert r.status_code == 409


class TestSelfLeave:
    def test_member_can_leave(self, client):
        """Self-removal: a non-admin can DELETE their own row without
        the project-admin gate. Last-admin guard still applies if
        they happen to also be the only admin."""
        owner = _signup(client, "owner@t.com")
        code = _invite_code(client, owner["access_token"])
        mate = _signup(client, "mate@t.com", invite=code)
        p = _create_project(client, owner["access_token"], "p")
        client.post(
            f"/portal/projects/{p}/members",
            headers=_auth(owner["access_token"]),
            json={"user_id": mate["user"]["id"], "role": "member"},
        )
        r = client.delete(
            f"/portal/projects/{p}/members/{mate['user']['id']}",
            headers=_auth(mate["access_token"]),
        )
        assert r.status_code == 200
