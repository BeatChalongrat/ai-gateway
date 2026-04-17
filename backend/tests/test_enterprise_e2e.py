"""P8-09: End-to-end enterprise control integration tests."""

import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_db_session, require_admin, ResolvedApiKey, get_resolved_api_key
from app.core.exceptions import QuotaExceededError
from app.domain.observability import RequestRecord
from app.main import app as main_app
from app.models.tenant import TenantORM
from app.models.project import ProjectORM
from app.services.budget_service import BudgetService
from app.services.quota_service import QuotaService
from app.services.request_log_service import RequestLogService
import app.models  # noqa: F401


@pytest.fixture()
def fresh_db(sqlite_engine):
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
    yield Session
    from tests.conftest import _integration_db_session
    main_app.dependency_overrides[get_db_session] = _integration_db_session


class TestRBACEnforcement:
    def test_admin_role_can_access_admin(self):
        """Conftest mock has role=admin — admin endpoints should work."""
        client = TestClient(main_app)
        resp = client.get("/admin/tenants", headers={"Authorization": "Bearer x"})
        assert resp.status_code == 200

    def test_public_api_still_works(self):
        """Public /v1 endpoints don't require admin role."""
        client = TestClient(main_app)
        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer x"},
            json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 200


class TestQuotaBudgetE2E:
    def test_quota_blocks_at_limit(self, fresh_db):
        s = fresh_db()
        tid, pid = str(uuid.uuid4()), str(uuid.uuid4())
        s.add(TenantORM(tenant_id=tid, name="T", slug=f"t-{tid[:8]}"))
        s.flush()
        s.add(ProjectORM(project_id=pid, tenant_id=tid, name="P", max_requests_per_day=1))
        s.flush()
        RequestLogService().persist(s, RequestRecord(
            request_id="r1", tenant_id=tid, project_id=pid,
        ))
        s.commit()
        s.close()

        s2 = fresh_db()
        with pytest.raises(QuotaExceededError):
            QuotaService().check_request_quota(s2, pid)
        s2.close()

    def test_budget_blocks_at_limit(self, fresh_db):
        s = fresh_db()
        tid, pid = str(uuid.uuid4()), str(uuid.uuid4())
        s.add(TenantORM(tenant_id=tid, name="T", slug=f"t-{tid[:8]}"))
        s.flush()
        s.add(ProjectORM(project_id=pid, tenant_id=tid, name="P", budget_limit=0.01))
        s.flush()
        RequestLogService().persist(s, RequestRecord(
            request_id="r1", tenant_id=tid, project_id=pid, estimated_cost_usd=0.02,
        ))
        s.commit()
        s.close()

        s2 = fresh_db()
        with pytest.raises(QuotaExceededError):
            BudgetService().check_budget(s2, pid)
        s2.close()


class TestAuditTrailE2E:
    def test_key_lifecycle_audited(self, fresh_db):
        client = TestClient(main_app)
        tid = client.post("/admin/tenants", json={"name": "T", "slug": f"t-{uuid.uuid4().hex[:8]}"}).json()["tenant_id"]
        pid = client.post("/admin/projects", json={"tenant_id": tid, "name": "P"}).json()["project_id"]
        kid = client.post("/admin/api-keys", json={"tenant_id": tid, "project_id": pid, "name": "K"}).json()["key_id"]
        client.delete(f"/admin/api-keys/{kid}")

        from app.services.admin_audit_service import AdminAuditService
        s = fresh_db()
        svc = AdminAuditService()
        creates = svc.list_by_action(s, "key_create")
        revokes = svc.list_by_action(s, "key_revoke")
        assert len(creates) >= 1
        assert len(revokes) >= 1
        s.close()
