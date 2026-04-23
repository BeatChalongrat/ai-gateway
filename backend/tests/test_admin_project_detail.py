"""Admin project-management endpoints (2026-04-24).

Covers the new surface added for the admin UI redesign:
  * GET /admin/projects/{id} — nested detail (members, counts, creator)
  * POST /admin/projects/{id}/members — add with role, reject cross-tenant
  * PATCH /admin/projects/{id}/members/{uid} — role change
  * DELETE /admin/projects/{id}/members/{uid} — detach
  * POST /admin/projects/{id}/archive — soft-delete
  * POST /admin/projects/{id}/restore — undo archive
  * GET /admin/projects?include_archived=true — toggle on list

These tests share the admin TestClient fixtures already used by
``test_admin_crud`` etc. and reuse the session login helper, so
CSRF is handled implicitly via the cookie jar.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.models.portal_user import PortalUserORM
from app.models.project import ProjectORM
from app.models.project_member import ProjectMemberORM
from app.models.tenant import TenantORM
from tests.conftest import _integration_engine


_Session = sessionmaker(bind=_integration_engine)


def _admin_login(client: TestClient) -> dict[str, str]:
    """No-op for the integration harness.

    tests/conftest.py already overrides ``require_admin_session`` with
    MOCK_AUTH so the endpoints don't actually check session cookies in
    the test suite; returning an empty headers dict keeps the call
    sites readable without plumbing conditional auth.
    """
    return {}


def _seed(db):
    """Minimal tenant + project + user for the member-CRUD suite."""
    import uuid

    # Tenant
    t = TenantORM(
        tenant_id="t-admin-proj",
        name="Test Tenant",
        slug="t-admin-proj",
        status="active",
        region="global",
        ner_enabled=False,
    )
    db.merge(t)

    # Users in the same tenant
    alice = PortalUserORM(
        id=str(uuid.uuid4()),
        email="a@admin-proj.test",
        password_hash="x",
        display_name="A",
        tenant_id="t-admin-proj",
        tenant_role="owner",
    )
    bob = PortalUserORM(
        id=str(uuid.uuid4()),
        email="b@admin-proj.test",
        password_hash="x",
        display_name="B",
        tenant_id="t-admin-proj",
        tenant_role="member",
    )
    # Cross-tenant user — used to assert the cross-tenant guard
    other_tenant = TenantORM(
        tenant_id="t-other",
        name="Other",
        slug="t-other",
        status="active",
        region="global",
        ner_enabled=False,
    )
    db.merge(other_tenant)
    chuck = PortalUserORM(
        id=str(uuid.uuid4()),
        email="c@other.test",
        password_hash="x",
        display_name="C",
        tenant_id="t-other",
        tenant_role="member",
    )
    db.add_all([alice, bob, chuck])

    # Project (creator = alice)
    proj = ProjectORM(
        project_id="p-admin-proj",
        tenant_id="t-admin-proj",
        name="Demo",
        policy_profile_id="default",
        portal_user_id=alice.id,
    )
    db.merge(proj)
    db.commit()
    return alice, bob, chuck


@pytest.fixture
def client():
    c = TestClient(app)
    yield c
    c.close()


@pytest.fixture
def users_and_headers(client):
    db = _Session()
    try:
        alice, bob, chuck = _seed(db)
        ids = (alice.id, bob.id, chuck.id)
    finally:
        db.close()
    headers = _admin_login(client)
    yield ids, headers

    # teardown — drop members + project + users we created
    db = _Session()
    try:
        db.query(ProjectMemberORM).filter(
            ProjectMemberORM.project_id == "p-admin-proj"
        ).delete(synchronize_session=False)
        db.query(ProjectORM).filter(
            ProjectORM.project_id == "p-admin-proj"
        ).delete(synchronize_session=False)
        db.query(PortalUserORM).filter(
            PortalUserORM.email.in_(
                ["a@admin-proj.test", "b@admin-proj.test", "c@other.test"]
            )
        ).delete(synchronize_session=False)
        db.query(TenantORM).filter(
            TenantORM.tenant_id.in_(["t-admin-proj", "t-other"])
        ).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


class TestProjectDetail:
    def test_detail_shape_includes_members_counts_creator(
        self, client, users_and_headers
    ):
        (alice_id, _, _), headers = users_and_headers
        r = client.get("/admin/projects/p-admin-proj", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body["project_id"] == "p-admin-proj"
        assert body["tenant_name"] == "Test Tenant"
        assert body["members"] == []  # no explicit members yet
        assert body["api_keys_count"] == 0
        assert body["request_count_last_7d"] == 0
        assert body["archived_at"] is None
        # Creator block populated from projects.portal_user_id
        assert body["created_by"] is not None
        assert body["created_by"]["user_id"] == alice_id


class TestAddMember:
    def test_owner_adds_in_tenant_user(self, client, users_and_headers):
        (_, bob_id, _), headers = users_and_headers
        r = client.post(
            "/admin/projects/p-admin-proj/members",
            headers=headers,
            json={"user_id": bob_id, "role": "member"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["user_id"] == bob_id
        assert body["role"] == "member"

        # Verify the GET reflects the new member with display name +
        # avatar_url populated via the portal_users join.
        r = client.get("/admin/projects/p-admin-proj", headers=headers)
        members = r.json()["members"]
        assert len(members) == 1
        assert members[0]["email"] == "b@admin-proj.test"

    def test_duplicate_add_is_409(self, client, users_and_headers):
        (_, bob_id, _), headers = users_and_headers
        client.post(
            "/admin/projects/p-admin-proj/members",
            headers=headers,
            json={"user_id": bob_id, "role": "member"},
        )
        r = client.post(
            "/admin/projects/p-admin-proj/members",
            headers=headers,
            json={"user_id": bob_id, "role": "member"},
        )
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "member_exists"

    def test_cross_tenant_add_rejected(self, client, users_and_headers):
        (_, _, chuck_id), headers = users_and_headers
        r = client.post(
            "/admin/projects/p-admin-proj/members",
            headers=headers,
            json={"user_id": chuck_id, "role": "member"},
        )
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "cross_tenant_member_add"

    def test_invalid_role_rejected(self, client, users_and_headers):
        (_, bob_id, _), headers = users_and_headers
        r = client.post(
            "/admin/projects/p-admin-proj/members",
            headers=headers,
            json={"user_id": bob_id, "role": "king"},
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "invalid_member_role"


class TestUpdateRemoveMember:
    def test_patch_role(self, client, users_and_headers):
        (_, bob_id, _), headers = users_and_headers
        client.post(
            "/admin/projects/p-admin-proj/members",
            headers=headers,
            json={"user_id": bob_id, "role": "viewer"},
        )
        r = client.patch(
            f"/admin/projects/p-admin-proj/members/{bob_id}",
            headers=headers,
            json={"role": "admin"},
        )
        assert r.status_code == 200
        assert r.json()["role"] == "admin"

    def test_delete_removes_row(self, client, users_and_headers):
        (_, bob_id, _), headers = users_and_headers
        client.post(
            "/admin/projects/p-admin-proj/members",
            headers=headers,
            json={"user_id": bob_id, "role": "member"},
        )
        r = client.delete(
            f"/admin/projects/p-admin-proj/members/{bob_id}",
            headers=headers,
        )
        assert r.status_code == 204
        # Second delete is 404, not idempotent — admin UI uses the
        # error to confirm the row really went away.
        r2 = client.delete(
            f"/admin/projects/p-admin-proj/members/{bob_id}",
            headers=headers,
        )
        assert r2.status_code == 404


class TestArchiveRestore:
    def test_archive_hides_from_default_list(self, client, users_and_headers):
        _, headers = users_and_headers
        r = client.post(
            "/admin/projects/p-admin-proj/archive", headers=headers
        )
        assert r.status_code == 200
        assert r.json()["archived_at"] is not None

        # Default list excludes archived
        r = client.get(
            "/admin/projects?tenant_id=t-admin-proj", headers=headers
        )
        names = [p["name"] for p in r.json()["items"]]
        assert "Demo" not in names

        # include_archived=true surfaces it
        r = client.get(
            "/admin/projects?tenant_id=t-admin-proj&include_archived=true",
            headers=headers,
        )
        names = [p["name"] for p in r.json()["items"]]
        assert "Demo" in names

    def test_restore_clears_archived_at(self, client, users_and_headers):
        _, headers = users_and_headers
        client.post("/admin/projects/p-admin-proj/archive", headers=headers)
        r = client.post(
            "/admin/projects/p-admin-proj/restore", headers=headers
        )
        assert r.status_code == 200
        assert r.json()["archived_at"] is None

        # Back in the default list
        r = client.get(
            "/admin/projects?tenant_id=t-admin-proj", headers=headers
        )
        names = [p["name"] for p in r.json()["items"]]
        assert "Demo" in names

    def test_archive_is_idempotent(self, client, users_and_headers):
        _, headers = users_and_headers
        first = client.post(
            "/admin/projects/p-admin-proj/archive", headers=headers
        ).json()["archived_at"]
        second = client.post(
            "/admin/projects/p-admin-proj/archive", headers=headers
        ).json()["archived_at"]
        # Both calls return a timestamp string. We compare by parsing
        # to datetime rather than equating strings because SQLite's
        # tz-dropping round-trip would otherwise make the second call's
        # string differ from the first (first = freshly-assigned tz-
        # aware datetime, second = reloaded DB row).
        from datetime import datetime

        assert first is not None and second is not None
        assert datetime.fromisoformat(first).replace(
            tzinfo=None
        ) == datetime.fromisoformat(second).replace(tzinfo=None)
