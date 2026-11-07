"""P7-03: Tenant/Project detail and filter endpoints tests."""

import uuid
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db_session
from app.core.db import Base
from app.main import app as main_app
import app.models  # noqa: F401


import pytest


@pytest.fixture()
def admin_client(sqlite_engine):
    Session = sessionmaker(bind=sqlite_engine)
    def _override():
        s = Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()
    main_app.dependency_overrides[get_db_session] = _override
    yield TestClient(main_app)
    # Restore conftest integration override
    from tests.conftest import _integration_db_session
    main_app.dependency_overrides[get_db_session] = _integration_db_session


class TestTenantDetail:
    def test_get_tenant_by_id(self, admin_client):
        client = admin_client
        # Create a tenant
        resp = client.post("/admin/tenants", json={"name": "T", "slug": f"t-{uuid.uuid4().hex[:8]}"})
        tid = resp.json()["tenant_id"]
        # Get it back
        resp = client.get(f"/admin/tenants/{tid}")
        assert resp.status_code == 200
        assert resp.json()["tenant_id"] == tid

    def test_get_missing_tenant_404(self, admin_client):
        client = admin_client
        resp = client.get("/admin/tenants/nonexistent")
        assert resp.status_code == 404


class TestProjectDetail:
    def _create_tenant(self, client):
        r = client.post("/admin/tenants", json={"name": "T", "slug": f"t-{uuid.uuid4().hex[:8]}"})
        return r.json()["tenant_id"]

    def test_get_project_by_id(self, admin_client):
        client = admin_client
        tid = self._create_tenant(client)
        resp = client.post("/admin/projects", json={"tenant_id": tid, "name": "P"})
        pid = resp.json()["project_id"]
        resp = client.get(f"/admin/projects/{pid}")
        assert resp.status_code == 200
        assert resp.json()["project_id"] == pid

    def test_get_missing_project_404(self, admin_client):
        client = admin_client
        resp = client.get("/admin/projects/nonexistent")
        assert resp.status_code == 404

    def test_filter_projects_by_tenant(self, admin_client):
        client = admin_client
        tid1 = self._create_tenant(client)
        tid2 = self._create_tenant(client)
        client.post("/admin/projects", json={"tenant_id": tid1, "name": "A"})
        client.post("/admin/projects", json={"tenant_id": tid2, "name": "B"})
        resp = client.get(f"/admin/projects?tenant_id={tid1}")
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["tenant_id"] == tid1

    def test_list_all_projects(self, admin_client):
        client = admin_client
        tid = self._create_tenant(client)
        client.post("/admin/projects", json={"tenant_id": tid, "name": "X"})
        client.post("/admin/projects", json={"tenant_id": tid, "name": "Y"})
        resp = client.get("/admin/projects")
        assert len(resp.json()["items"]) >= 2
