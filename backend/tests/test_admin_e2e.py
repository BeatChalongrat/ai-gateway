"""P7-09: End-to-end admin API integration tests.

Full lifecycle: create tenant → create project → create API key → verify
through list/detail endpoints → revoke key → verify status.
"""

import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_db_session
from app.main import app as main_app
import app.models  # noqa: F401


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
    from tests.conftest import _integration_db_session
    main_app.dependency_overrides[get_db_session] = _integration_db_session


class TestAdminE2ELifecycle:
    def test_full_tenant_project_key_lifecycle(self, admin_client):
        c = admin_client

        # 1. Create tenant
        t = c.post("/admin/tenants", json={
            "name": "Acme Corp", "slug": f"acme-{uuid.uuid4().hex[:6]}"
        })
        assert t.status_code == 201
        tid = t.json()["tenant_id"]

        # 2. Get tenant detail
        t_detail = c.get(f"/admin/tenants/{tid}")
        assert t_detail.status_code == 200
        assert t_detail.json()["name"] == "Acme Corp"

        # 3. Create project under tenant
        p = c.post("/admin/projects", json={
            "tenant_id": tid, "name": "Production", "policy_profile_id": "strict"
        })
        assert p.status_code == 201
        pid = p.json()["project_id"]

        # 4. Get project detail
        p_detail = c.get(f"/admin/projects/{pid}")
        assert p_detail.status_code == 200
        assert p_detail.json()["policy_profile_id"] == "strict"

        # 5. Filter projects by tenant
        projects = c.get(f"/admin/projects?tenant_id={tid}").json()["items"]
        assert len(projects) == 1

        # 6. Create API key
        k = c.post("/admin/api-keys", json={
            "tenant_id": tid, "project_id": pid, "name": "CI Key"
        })
        assert k.status_code == 201
        kid = k.json()["key_id"]
        raw_key = k.json()["key"]
        assert raw_key.startswith("gw_")

        # 7. List keys (plaintext key not exposed)
        keys = c.get(f"/admin/api-keys?project_id={pid}").json()["items"]
        assert len(keys) == 1
        assert "key" not in keys[0]

        # 8. Revoke key
        r = c.delete(f"/admin/api-keys/{kid}")
        assert r.status_code == 204

        # 9. Verify revoked status
        k_detail = c.get(f"/admin/api-keys/{kid}")
        assert k_detail.json()["status"] == "revoked"


class TestAdminE2EDiscovery:
    def test_providers_endpoint(self, admin_client):
        resp = admin_client.get("/admin/providers")
        assert resp.status_code == 200
        assert len(resp.json()["items"]) > 0

    def test_models_endpoint(self, admin_client):
        resp = admin_client.get("/admin/models")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert any(i["provider"] == "openai" for i in items)

    def test_policies_endpoint(self, admin_client):
        resp = admin_client.get("/admin/policies")
        assert resp.status_code == 200


class TestAdminE2EErrors:
    def test_create_project_missing_tenant_404(self, admin_client):
        resp = admin_client.post("/admin/projects", json={
            "tenant_id": "nonexistent", "name": "P"
        })
        assert resp.status_code == 404

    def test_revoke_missing_key_404(self, admin_client):
        resp = admin_client.delete(f"/admin/api-keys/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_get_missing_tenant_404(self, admin_client):
        resp = admin_client.get("/admin/tenants/nonexistent")
        assert resp.status_code == 404
