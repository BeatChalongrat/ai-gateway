"""Phase 2A — Commit 5: P0-5 / P0-6 tenant-isolation regression (TDD).

These tests pin the P0 exploits replayed against the live admin
routes:

* P0-5: tenant-A admin GETs /admin/projects → must NOT see tenant-B's
  projects, regardless of tenant_id query parameter value (or absence).

* P0-6: tenant-A admin GETs /admin/api-keys → must NOT see tenant-B's
  api-keys, regardless of project_id query parameter value.

Global admins (role=ADMIN) are still allowed to see all tenants'
data — that's the operator use case. Tenant-scoped admins
(role=TENANT_ADMIN or lower with a tenant_id set) get a forced
filter to their own tenant.
"""

from __future__ import annotations

import uuid

import bcrypt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from app.core.db import Base
    import app.models  # noqa: F401

    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def seeded(engine):
    """Two tenants, two projects each, one API key per project, plus
    one admin user per scope: tenant-a scoped, tenant-b scoped, global.
    """
    import uuid

    from app.models.admin_user import AdminUserORM
    from app.models.api_key import ApiKeyORM
    from app.models.project import ProjectORM
    from app.models.tenant import TenantORM

    Session = sessionmaker(bind=engine)
    db = Session()

    db.add_all(
        [
            TenantORM(tenant_id="tenant-a", name="Tenant A", slug="a"),
            TenantORM(tenant_id="tenant-b", name="Tenant B", slug="b"),
        ]
    )
    db.flush()

    pa = ProjectORM(project_id="proj-a1", tenant_id="tenant-a", name="A1", policy_profile_id="default")
    pb = ProjectORM(project_id="proj-b1", tenant_id="tenant-b", name="B1", policy_profile_id="default")
    db.add_all([pa, pb])
    db.flush()

    db.add_all(
        [
            ApiKeyORM(
                key_id="key-a1", tenant_id="tenant-a", project_id="proj-a1",
                key_prefix="gw_a_", key_hash="xxx", name="A1", status="active",
            ),
            ApiKeyORM(
                key_id="key-b1", tenant_id="tenant-b", project_id="proj-b1",
                key_prefix="gw_b_", key_hash="yyy", name="B1", status="active",
            ),
        ]
    )

    pw = bcrypt.hashpw(b"pw", bcrypt.gensalt(rounds=4)).decode("utf-8")
    alice = AdminUserORM(
        id=str(uuid.uuid4()), username="alice", email="alice@a.com",
        hashed_password=pw, role="tenant_admin", is_active=True,
        org_id="tenant-a",
    )
    bob = AdminUserORM(
        id=str(uuid.uuid4()), username="bob", email="bob@b.com",
        hashed_password=pw, role="tenant_admin", is_active=True,
        org_id="tenant-b",
    )
    root = AdminUserORM(
        id=str(uuid.uuid4()), username="root", email="root@global.com",
        hashed_password=pw, role="admin", is_active=True,
        org_id=None,
    )
    db.add_all([alice, bob, root])
    db.commit()

    # Snapshot the fields we need BEFORE closing the session so the
    # tests don't stumble onto detached-instance loads.
    snap = {
        "alice": {"id": alice.id, "role": alice.role, "org_id": alice.org_id},
        "bob":   {"id": bob.id,   "role": bob.role,   "org_id": bob.org_id},
        "root":  {"id": root.id,  "role": root.role,  "org_id": root.org_id},
    }
    db.close()
    return snap


@pytest.fixture
def client(engine, seeded):
    from fastapi import FastAPI

    from app.api.deps import ResolvedApiKey, get_db_session, require_admin
    from app.api.routes.admin import router as admin_router
    from app.core.exceptions import GatewayError
    from app.middleware.error_handler import (
        handle_gateway_error,
        handle_unhandled_exception,
    )

    app = FastAPI()
    app.add_exception_handler(GatewayError, handle_gateway_error)
    app.add_exception_handler(Exception, handle_unhandled_exception)

    Session = sessionmaker(bind=engine)

    def _db():
        s = Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    app.dependency_overrides[get_db_session] = _db
    app.include_router(admin_router, prefix="/admin")

    def _make_tc(user: dict):
        def _auth_dep():
            return ResolvedApiKey(
                key_id=f"test:{user['id']}",
                tenant_id=user["org_id"] or "__global__",
                project_id="__admin__",
                key_prefix="test_",
                role=user["role"],
            )
        # Override require_admin for this client.
        app.dependency_overrides[require_admin] = _auth_dep
        return TestClient(app)

    return _make_tc


# ---------------------------------------------------------------------------
# P0-5: /admin/projects leak
# ---------------------------------------------------------------------------


class TestP0_5Projects:
    def test_tenant_a_cannot_see_tenant_b_projects_via_no_filter(
        self, client, seeded
    ):
        """The bug: GET /admin/projects without tenant_id returns ALL."""
        tc = client(seeded["alice"])
        r = tc.get("/admin/projects")
        assert r.status_code == 200
        ids = {p["project_id"] for p in r.json()["items"]}
        assert "proj-a1" in ids
        assert "proj-b1" not in ids, (
            "P0-5 regression: tenant-A admin saw tenant-B's project"
        )

    def test_tenant_a_cannot_spoof_tenant_b_via_query(self, client, seeded):
        """Even if the caller forges ?tenant_id=tenant-b, they get either
        403 or their own tenant's rows — never tenant-b's."""
        tc = client(seeded["alice"])
        r = tc.get("/admin/projects?tenant_id=tenant-b")
        # Acceptable: either a 403 (forbidden) or a 200 with filter forced
        # back to tenant-a (returning zero B rows).
        if r.status_code == 200:
            ids = {p["project_id"] for p in r.json()["items"]}
            assert "proj-b1" not in ids
        else:
            assert r.status_code == 403

    def test_global_admin_sees_all_projects(self, client, seeded):
        tc = client(seeded["root"])
        r = tc.get("/admin/projects")
        assert r.status_code == 200
        ids = {p["project_id"] for p in r.json()["items"]}
        assert "proj-a1" in ids
        assert "proj-b1" in ids

    def test_global_admin_can_filter_by_any_tenant(self, client, seeded):
        tc = client(seeded["root"])
        r = tc.get("/admin/projects?tenant_id=tenant-b")
        assert r.status_code == 200
        ids = {p["project_id"] for p in r.json()["items"]}
        assert ids == {"proj-b1"}


# ---------------------------------------------------------------------------
# P0-6: /admin/api-keys leak
# ---------------------------------------------------------------------------


class TestP0_6ApiKeys:
    def test_tenant_a_cannot_see_tenant_b_keys_via_no_filter(self, client, seeded):
        tc = client(seeded["alice"])
        r = tc.get("/admin/api-keys")
        assert r.status_code == 200
        ids = {k["key_id"] for k in r.json()["items"]}
        assert "key-a1" in ids
        assert "key-b1" not in ids, (
            "P0-6 regression: tenant-A admin saw tenant-B's api-key"
        )

    def test_tenant_a_cannot_spoof_project_filter(self, client, seeded):
        tc = client(seeded["alice"])
        r = tc.get("/admin/api-keys?project_id=proj-b1")
        if r.status_code == 200:
            ids = {k["key_id"] for k in r.json()["items"]}
            assert "key-b1" not in ids
        else:
            assert r.status_code == 403

    def test_global_admin_sees_all_keys(self, client, seeded):
        tc = client(seeded["root"])
        r = tc.get("/admin/api-keys")
        assert r.status_code == 200
        ids = {k["key_id"] for k in r.json()["items"]}
        assert {"key-a1", "key-b1"} <= ids


# ---------------------------------------------------------------------------
# Bonus: /admin/projects/{id} direct fetch must still enforce
# ---------------------------------------------------------------------------


class TestDirectFetchIsolation:
    def test_tenant_a_cannot_fetch_b_project_by_id(self, client, seeded):
        tc = client(seeded["alice"])
        r = tc.get("/admin/projects/proj-b1")
        # check_tenant_access → AuthorizationError → 403.
        assert r.status_code == 403

    def test_tenant_a_cannot_fetch_b_apikey_by_id(self, client, seeded):
        tc = client(seeded["alice"])
        r = tc.get("/admin/api-keys/key-b1")
        assert r.status_code == 403
