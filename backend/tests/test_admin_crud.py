"""P4-07: Admin CRUD endpoint tests.

Verifies:
- Tenant list/create
- Project list/create (validates tenant exists)
- API key list/create/revoke
- Create API key returns plaintext `key` only on creation
- Revoke unknown key returns 404
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db_session
from app.core.db import Base
from app.core.exceptions import NotFoundError
from app.main import app as main_app
from app.middleware.error_handler import handle_gateway_error
import app.models  # noqa: F401


@pytest.fixture()
def admin_client(sqlite_engine):
    """TestClient with a fresh DB for each test."""
    Session = sessionmaker(bind=sqlite_engine)

    def _override():
        session = Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    main_app.dependency_overrides[get_db_session] = _override
    yield TestClient(main_app)
    # Restore the integration override from conftest
    from tests.conftest import _integration_db_session
    main_app.dependency_overrides[get_db_session] = _integration_db_session


# ---------------------------------------------------------------------------
# Tenant CRUD
# ---------------------------------------------------------------------------


class TestTenantList:
    def test_empty_list(self, admin_client):
        resp = admin_client.get("/admin/tenants")
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_list_after_create(self, admin_client):
        admin_client.post(
            "/admin/tenants",
            json={"name": "Acme", "slug": "acme"},
        )
        resp = admin_client.get("/admin/tenants")
        assert len(resp.json()["items"]) == 1
        assert resp.json()["items"][0]["name"] == "Acme"


class TestTenantCreate:
    def test_create_returns_201(self, admin_client):
        resp = admin_client.post(
            "/admin/tenants",
            json={"name": "Acme", "slug": "acme"},
        )
        assert resp.status_code == 201

    def test_create_returns_fields(self, admin_client):
        resp = admin_client.post(
            "/admin/tenants",
            json={"name": "Acme", "slug": "acme", "region": "eu"},
        )
        body = resp.json()
        assert body["name"] == "Acme"
        assert body["slug"] == "acme"
        assert body["region"] == "eu"
        assert body["status"] == "active"
        assert "tenant_id" in body

    def test_create_generates_uuid(self, admin_client):
        resp = admin_client.post(
            "/admin/tenants",
            json={"name": "T", "slug": "t"},
        )
        tid = resp.json()["tenant_id"]
        uuid.UUID(tid)  # validates format


# ---------------------------------------------------------------------------
# Project CRUD
# ---------------------------------------------------------------------------


class TestProjectCreate:
    def _create_tenant(self, client):
        resp = client.post(
            "/admin/tenants",
            json={"name": "T", "slug": f"t-{uuid.uuid4().hex[:8]}"},
        )
        return resp.json()["tenant_id"]

    def test_create_returns_201(self, admin_client):
        tid = self._create_tenant(admin_client)
        resp = admin_client.post(
            "/admin/projects",
            json={"tenant_id": tid, "name": "My Project"},
        )
        assert resp.status_code == 201

    def test_create_returns_fields(self, admin_client):
        tid = self._create_tenant(admin_client)
        resp = admin_client.post(
            "/admin/projects",
            json={"tenant_id": tid, "name": "P", "policy_profile_id": "strict"},
        )
        body = resp.json()
        assert body["name"] == "P"
        assert body["tenant_id"] == tid
        assert body["policy_profile_id"] == "strict"

    def test_create_with_missing_tenant_returns_404(self, admin_client):
        resp = admin_client.post(
            "/admin/projects",
            json={"tenant_id": "nonexistent", "name": "P"},
        )
        assert resp.status_code == 404

    def test_list_projects(self, admin_client):
        tid = self._create_tenant(admin_client)
        admin_client.post(
            "/admin/projects",
            json={"tenant_id": tid, "name": "A"},
        )
        admin_client.post(
            "/admin/projects",
            json={"tenant_id": tid, "name": "B"},
        )
        resp = admin_client.get("/admin/projects")
        assert len(resp.json()["items"]) == 2


# ---------------------------------------------------------------------------
# API Key CRUD
# ---------------------------------------------------------------------------


class TestApiKeyCRUD:
    def _seed_tenant_and_project(self, client):
        tid = client.post(
            "/admin/tenants",
            json={"name": "T", "slug": f"t-{uuid.uuid4().hex[:8]}"},
        ).json()["tenant_id"]
        pid = client.post(
            "/admin/projects",
            json={"tenant_id": tid, "name": "P"},
        ).json()["project_id"]
        return tid, pid

    def test_create_returns_201(self, admin_client):
        tid, pid = self._seed_tenant_and_project(admin_client)
        resp = admin_client.post(
            "/admin/api-keys",
            json={"tenant_id": tid, "project_id": pid, "name": "CI Key"},
        )
        assert resp.status_code == 201

    def test_create_returns_key(self, admin_client):
        tid, pid = self._seed_tenant_and_project(admin_client)
        resp = admin_client.post(
            "/admin/api-keys",
            json={"tenant_id": tid, "project_id": pid, "name": "CI Key"},
        )
        body = resp.json()
        assert "key" in body
        assert body["key"].startswith("gw_")
        assert body["status"] == "active"
        assert body["name"] == "CI Key"

    def test_list_does_not_contain_key(self, admin_client):
        tid, pid = self._seed_tenant_and_project(admin_client)
        admin_client.post(
            "/admin/api-keys",
            json={"tenant_id": tid, "project_id": pid, "name": "K"},
        )
        resp = admin_client.get("/admin/api-keys")
        items = resp.json()["items"]
        assert len(items) == 1
        assert "key" not in items[0]
        assert "key_prefix" in items[0]

    def test_revoke_returns_204(self, admin_client):
        tid, pid = self._seed_tenant_and_project(admin_client)
        key_id = admin_client.post(
            "/admin/api-keys",
            json={"tenant_id": tid, "project_id": pid, "name": "K"},
        ).json()["key_id"]
        resp = admin_client.delete(f"/admin/api-keys/{key_id}")
        assert resp.status_code == 204

    def test_revoke_unknown_returns_404(self, admin_client):
        resp = admin_client.delete(f"/admin/api-keys/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_revoked_key_shows_status(self, admin_client):
        tid, pid = self._seed_tenant_and_project(admin_client)
        key_id = admin_client.post(
            "/admin/api-keys",
            json={"tenant_id": tid, "project_id": pid, "name": "K"},
        ).json()["key_id"]
        admin_client.delete(f"/admin/api-keys/{key_id}")
        items = admin_client.get("/admin/api-keys").json()["items"]
        revoked = [k for k in items if k["key_id"] == key_id]
        assert revoked[0]["status"] == "revoked"
