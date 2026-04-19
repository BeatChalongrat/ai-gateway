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


@pytest.fixture()
def bulk_client(sqlite_engine):
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

    s = Session()
    tid, pid = str(uuid.uuid4()), str(uuid.uuid4())
    s.add(TenantORM(tenant_id=tid, name="TB", slug=f"tb-{tid[:8]}"))
    s.flush()
    s.add(ProjectORM(project_id=pid, tenant_id=tid, name="PB"))
    s.flush()
    svc = RequestLogService()
    for i in range(150):
        svc.persist(s, RequestRecord(
            request_id=f"req_bulk_{i:03d}",
            tenant_id=tid, project_id=pid,
            provider="openai", model="gpt-4o", latency_ms=100,
            prompt_tokens=10, total_tokens=15,
        ))
    s.commit()
    s.close()
    yield TestClient(main_app), tid, pid
    from tests.conftest import _integration_db_session
    main_app.dependency_overrides[get_db_session] = _integration_db_session


class TestRequestsPagination:
    def test_default_limit_is_100(self, bulk_client):
        client, _, _ = bulk_client
        body = client.get("/admin/requests").json()
        assert len(body["items"]) == 100
        assert body["total"] == 150
        assert body["limit"] == 100
        assert body["offset"] == 0

    def test_limit_above_default_returns_more(self, bulk_client):
        client, _, _ = bulk_client
        body = client.get("/admin/requests?limit=200").json()
        assert body["total"] == 150
        assert len(body["items"]) == 150

    def test_limit_below_default_caps_items(self, bulk_client):
        client, _, _ = bulk_client
        body = client.get("/admin/requests?limit=50").json()
        assert len(body["items"]) == 50
        assert body["total"] == 150

    def test_offset_pages_past_first_slice(self, bulk_client):
        client, _, _ = bulk_client
        first = client.get("/admin/requests?limit=10&offset=0").json()
        second = client.get("/admin/requests?limit=10&offset=10").json()
        first_ids = {i["request_id"] for i in first["items"]}
        second_ids = {i["request_id"] for i in second["items"]}
        assert first_ids.isdisjoint(second_ids)

    def test_limit_over_cap_rejected(self, bulk_client):
        client, _, _ = bulk_client
        resp = client.get("/admin/requests?limit=5001")
        assert resp.status_code == 422

    def test_negative_offset_rejected(self, bulk_client):
        client, _, _ = bulk_client
        resp = client.get("/admin/requests?offset=-1")
        assert resp.status_code == 422

    def test_project_filter_pagination(self, bulk_client):
        client, _, pid = bulk_client
        body = client.get(f"/admin/requests?project_id={pid}&limit=200").json()
        assert body["total"] == 150
        assert len(body["items"]) == 150
