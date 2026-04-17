"""P8-08: Sensitive action audit trail tests."""

import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_db_session
from app.main import app as main_app
from app.models.admin_audit_log import AdminAuditLogORM
from app.services.admin_audit_service import AdminAuditService
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
    yield TestClient(main_app), Session
    from tests.conftest import _integration_db_session
    main_app.dependency_overrides[get_db_session] = _integration_db_session


class TestSensitiveActionAudit:
    def test_key_create_audited(self, admin_client):
        client, Session = admin_client
        tid = client.post("/admin/tenants", json={"name": "T", "slug": f"t-{uuid.uuid4().hex[:8]}"}).json()["tenant_id"]
        pid = client.post("/admin/projects", json={"tenant_id": tid, "name": "P"}).json()["project_id"]
        client.post("/admin/api-keys", json={"tenant_id": tid, "project_id": pid, "name": "K"})

        s = Session()
        svc = AdminAuditService()
        entries = svc.list_by_action(s, "key_create")
        assert len(entries) >= 1
        assert entries[0].resource_type == "api_key"
        s.close()

    def test_key_revoke_audited(self, admin_client):
        client, Session = admin_client
        tid = client.post("/admin/tenants", json={"name": "T", "slug": f"t-{uuid.uuid4().hex[:8]}"}).json()["tenant_id"]
        pid = client.post("/admin/projects", json={"tenant_id": tid, "name": "P"}).json()["project_id"]
        kid = client.post("/admin/api-keys", json={"tenant_id": tid, "project_id": pid, "name": "K"}).json()["key_id"]
        client.delete(f"/admin/api-keys/{kid}")

        s = Session()
        svc = AdminAuditService()
        entries = svc.list_by_action(s, "key_revoke")
        assert len(entries) >= 1
        assert entries[0].resource_id == kid
        s.close()
