"""P7-06: Requests / Audit viewer endpoint tests."""

import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_db_session
from app.domain.observability import RequestRecord, RequestStatus
from app.main import app as main_app
from app.services.request_log_service import RequestLogService
from app.models.tenant import TenantORM
from app.models.project import ProjectORM
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
    # Seed data
    s = Session()
    tid, pid = str(uuid.uuid4()), str(uuid.uuid4())
    s.add(TenantORM(tenant_id=tid, name="T", slug=f"t-{tid[:8]}"))
    s.flush()
    s.add(ProjectORM(project_id=pid, tenant_id=tid, name="P"))
    s.flush()
    svc = RequestLogService()
    svc.persist(s, RequestRecord(
        request_id="req_test_1", tenant_id=tid, project_id=pid,
        provider="openai", model="gpt-4o", latency_ms=100,
        prompt_tokens=10, total_tokens=15,
    ))
    s.commit()
    s.close()
    yield TestClient(main_app), tid, pid
    from tests.conftest import _integration_db_session
    main_app.dependency_overrides[get_db_session] = _integration_db_session


class TestRequestViewer:
    def test_list_requests(self, admin_client):
        client, _, _ = admin_client
        resp = client.get("/admin/requests")
        assert resp.status_code == 200
        assert len(resp.json()["items"]) >= 1

    def test_get_request_detail(self, admin_client):
        client, _, _ = admin_client
        resp = client.get("/admin/requests/req_test_1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["request_id"] == "req_test_1"
        assert body["provider"] == "openai"
        assert body["latency_ms"] == 100
        assert "prompt_tokens" in body
        assert "total_tokens" in body

    def test_missing_request_404(self, admin_client):
        client, _, _ = admin_client
        resp = client.get("/admin/requests/nonexistent")
        assert resp.status_code == 404

    def test_filter_by_project(self, admin_client):
        client, _, pid = admin_client
        resp = client.get(f"/admin/requests?project_id={pid}")
        items = resp.json()["items"]
        assert all(i["request_id"] == "req_test_1" for i in items)

    def test_detail_has_no_raw_content(self, admin_client):
        client, _, _ = admin_client
        body = client.get("/admin/requests/req_test_1").json()
        for key in body:
            assert "content" not in key.lower() or key == "response_content"
