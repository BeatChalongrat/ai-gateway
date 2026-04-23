"""Phase 3 Commit 21 — portal_projects cross-tenant isolation.

Verifies every portal-projects endpoint refuses access when the
caller's tenant differs from the target resource's tenant. These are
the first integration tests that exercise Phase 3's JWT-driven tenant
scoping end-to-end.

The baseline behaviour (_own_project filtering by portal_user_id) was
already in place. Phase 3 makes the stronger claim: even if a user
could somehow reference a cross-tenant project_id by name, the
isolation holds because writes derive tenant_id from the session,
not the request body.
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
from tests.conftest import _integration_engine


_Session = sessionmaker(bind=_integration_engine)


@pytest.fixture(autouse=True)
def _clean():
    """Wipe everything the portal creates between tests so tenants
    don't accumulate. The seeded ``test-tenant`` row (conftest) and
    its ``test-project`` stay — they're used by other test files.
    """
    db = _Session()
    try:
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


def _signup(client, email: str) -> dict:
    r = client.post(
        "/portal/auth/signup",
        json={"email": email, "password": "Test1234!"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestCrossTenantProjectIsolation:
    def test_two_users_land_in_different_tenants(self, client):
        a = _signup(client, "a@t.com")
        b = _signup(client, "b@t.com")
        assert a["user"]["id"] != b["user"]["id"]
        # Peek at DB to verify they ended up in different tenants.
        db = _Session()
        try:
            ua = db.get(PortalUserORM, a["user"]["id"])
            ub = db.get(PortalUserORM, b["user"]["id"])
            assert ua.tenant_id != ub.tenant_id
            assert ua.tenant_role == "owner"
            assert ub.tenant_role == "owner"
        finally:
            db.close()

    def test_tenant_a_cannot_list_tenant_b_projects(self, client):
        a = _signup(client, "a@t.com")
        b = _signup(client, "b@t.com")
        # A creates a project.
        client.post(
            "/portal/projects",
            headers=_auth(a["access_token"]),
            json={"name": "A-proj"},
        )
        # B lists their projects — must not see A's.
        r = client.get("/portal/projects", headers=_auth(b["access_token"]))
        assert r.status_code == 200
        names = [p["name"] for p in r.json()["projects"]]
        assert "A-proj" not in names

    def test_tenant_b_cannot_fetch_tenant_a_project_by_id(self, client):
        a = _signup(client, "a@t.com")
        b = _signup(client, "b@t.com")
        create = client.post(
            "/portal/projects",
            headers=_auth(a["access_token"]),
            json={"name": "A-proj"},
        )
        a_proj_id = create.json()["id"]
        r = client.get(
            f"/portal/projects/{a_proj_id}",
            headers=_auth(b["access_token"]),
        )
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "project_not_found"

    def test_tenant_b_cannot_patch_tenant_a_project(self, client):
        a = _signup(client, "a@t.com")
        b = _signup(client, "b@t.com")
        create = client.post(
            "/portal/projects",
            headers=_auth(a["access_token"]),
            json={"name": "A-proj"},
        )
        a_proj_id = create.json()["id"]
        r = client.patch(
            f"/portal/projects/{a_proj_id}",
            headers=_auth(b["access_token"]),
            json={"name": "hijacked"},
        )
        assert r.status_code == 404

    def test_tenant_b_cannot_delete_tenant_a_project(self, client):
        a = _signup(client, "a@t.com")
        b = _signup(client, "b@t.com")
        create = client.post(
            "/portal/projects",
            headers=_auth(a["access_token"]),
            json={"name": "A-proj"},
        )
        a_proj_id = create.json()["id"]
        r = client.delete(
            f"/portal/projects/{a_proj_id}",
            headers=_auth(b["access_token"]),
        )
        assert r.status_code == 404

    def test_tenant_b_cannot_list_keys_for_tenant_a_project(self, client):
        a = _signup(client, "a@t.com")
        b = _signup(client, "b@t.com")
        create = client.post(
            "/portal/projects",
            headers=_auth(a["access_token"]),
            json={"name": "A-proj"},
        )
        a_proj_id = create.json()["id"]
        r = client.get(
            f"/portal/projects/{a_proj_id}/keys",
            headers=_auth(b["access_token"]),
        )
        assert r.status_code == 404

    def test_tenant_b_cannot_create_key_in_tenant_a_project(self, client):
        a = _signup(client, "a@t.com")
        b = _signup(client, "b@t.com")
        create = client.post(
            "/portal/projects",
            headers=_auth(a["access_token"]),
            json={"name": "A-proj"},
        )
        a_proj_id = create.json()["id"]
        r = client.post(
            f"/portal/projects/{a_proj_id}/keys",
            headers=_auth(b["access_token"]),
            json={"name": "hijack-key"},
        )
        assert r.status_code == 404

    def test_new_project_lands_in_callers_own_tenant(self, client):
        a = _signup(client, "a@t.com")
        r = client.post(
            "/portal/projects",
            headers=_auth(a["access_token"]),
            json={"name": "A-proj"},
        )
        assert r.status_code == 200
        body = r.json()
        # The response echoes the tenant the project landed in; must
        # be A's tenant, not any stale demo-tenant-000 default.
        db = _Session()
        try:
            ua = db.get(PortalUserORM, a["user"]["id"])
            assert body["tenant_id"] == ua.tenant_id
            assert body["tenant_id"] != "demo-tenant-000"
        finally:
            db.close()

    def test_invited_member_projects_stay_in_owners_tenant(self, client):
        """Redemption path: the invited user becomes a member in the
        owner's tenant, and any project they create should land there
        (same tenant) — not in a new tenant."""
        owner = _signup(client, "owner@t.com")
        # Generate an invite via the service directly (portal_team
        # routes land in Commit 22 — this commit exercises the
        # isolation contract already present in portal_projects).
        from app.services.portal import tenant_service

        db = _Session()
        try:
            owner_user = db.get(PortalUserORM, owner["user"]["id"])
            invite = tenant_service.generate_invite(
                db,
                tenant_id=owner_user.tenant_id,
                creator_user_id=owner_user.id,
            )
            db.commit()
            code = invite.code
            owner_tenant = owner_user.tenant_id
        finally:
            db.close()

        member = client.post(
            "/portal/auth/signup",
            json={
                "email": "member@t.com",
                "password": "Test1234!",
                "invite_code": code,
            },
        ).json()

        r = client.post(
            "/portal/projects",
            headers=_auth(member["access_token"]),
            json={"name": "member-proj"},
        )
        assert r.status_code == 200
        assert r.json()["tenant_id"] == owner_tenant

        # Owner's list should now include the member's project (same
        # tenant, but different portal_user_id — so owner currently
        # doesn't see it via the portal_user_id filter). The important
        # invariant: member's project did NOT create a second tenant.
        db = _Session()
        try:
            distinct_tenants = {
                t
                for (t,) in db.query(ProjectORM.tenant_id)
                .filter(ProjectORM.portal_user_id.isnot(None))
                .distinct()
            }
            assert distinct_tenants == {owner_tenant}
        finally:
            db.close()
