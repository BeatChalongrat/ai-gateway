"""P7-04: API key management detail tests."""

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


def _setup(client):
    tid = client.post("/admin/tenants", json={"name": "T", "slug": f"t-{uuid.uuid4().hex[:8]}"}).json()["tenant_id"]
    pid = client.post("/admin/projects", json={"tenant_id": tid, "name": "P"}).json()["project_id"]
    return tid, pid


class TestApiKeyDetail:
    def test_get_by_id(self, admin_client):
        tid, pid = _setup(admin_client)
        kid = admin_client.post("/admin/api-keys", json={"tenant_id": tid, "project_id": pid, "name": "K"}).json()["key_id"]
        resp = admin_client.get(f"/admin/api-keys/{kid}")
        assert resp.status_code == 200
        assert resp.json()["key_id"] == kid
        assert "key" not in resp.json()

    def test_missing_key_404(self, admin_client):
        resp = admin_client.get(f"/admin/api-keys/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_filter_by_project(self, admin_client):
        tid, pid = _setup(admin_client)
        admin_client.post("/admin/api-keys", json={"tenant_id": tid, "project_id": pid, "name": "K1"})
        resp = admin_client.get(f"/admin/api-keys?project_id={pid}")
        assert len(resp.json()["items"]) == 1

    def test_revoked_key_shows_status(self, admin_client):
        tid, pid = _setup(admin_client)
        kid = admin_client.post("/admin/api-keys", json={"tenant_id": tid, "project_id": pid, "name": "K"}).json()["key_id"]
        admin_client.delete(f"/admin/api-keys/{kid}")
        resp = admin_client.get(f"/admin/api-keys/{kid}")
        assert resp.json()["status"] == "revoked"
