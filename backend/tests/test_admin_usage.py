"""P7-07: Usage / Cost dashboard endpoint tests."""

import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_db_session
from app.domain.observability import RequestRecord
from app.main import app as main_app
from app.models.tenant import TenantORM
from app.models.project import ProjectORM
from app.services.request_log_service import RequestLogService
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
    s = Session()
    tid, pid = str(uuid.uuid4()), str(uuid.uuid4())
    s.add(TenantORM(tenant_id=tid, name="T", slug=f"t-{tid[:8]}"))
    s.flush()
    s.add(ProjectORM(project_id=pid, tenant_id=tid, name="P"))
    s.flush()
    svc = RequestLogService()
    svc.persist(s, RequestRecord(
        request_id="req_u1", tenant_id=tid, project_id=pid,
        provider="openai", model="gpt-4o-mini",
        total_tokens=100, estimated_cost_usd=0.001,
    ))
    svc.persist(s, RequestRecord(
        request_id="req_u2", tenant_id=tid, project_id=pid,
        provider="openai", model="gpt-4o-mini",
        total_tokens=200, estimated_cost_usd=0.002,
    ))
    s.commit()
    s.close()
    yield TestClient(main_app), pid
    from tests.conftest import _integration_db_session
    main_app.dependency_overrides[get_db_session] = _integration_db_session


class TestUsageSummaryEndpoint:
    def test_returns_200(self, admin_client):
        client, pid = admin_client
        resp = client.get(f"/admin/usage/summary?project_id={pid}")
        assert resp.status_code == 200

    def test_returns_aggregated_data(self, admin_client):
        client, pid = admin_client
        body = client.get(f"/admin/usage/summary?project_id={pid}").json()
        assert body["project_id"] == pid
        assert body["total_requests"] == 2
        assert body["total_tokens"] == 300
        assert body["total_estimated_cost_usd"] == 0.003

    def test_empty_project_returns_zeros(self, admin_client):
        client, _ = admin_client
        body = client.get("/admin/usage/summary?project_id=nonexistent").json()
        assert body["total_requests"] == 0
        assert body["total_tokens"] == 0

    def test_aggregate_without_project_id_returns_totals(self, admin_client):
        """Omitting ``project_id`` used to 422; now returns aggregate
        totals across every project via ``usage_summary_all``."""
        client, _ = admin_client
        resp = client.get("/admin/usage/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body["project_id"] is None
        # Fixture seeded 2 rows (100 + 200 tokens, 0.001 + 0.002 cost).
        assert body["total_requests"] == 2
        assert body["total_tokens"] == 300
        assert body["total_estimated_cost_usd"] == 0.003
