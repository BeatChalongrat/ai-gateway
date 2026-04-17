"""P6-08: Request log query layer tests."""

import uuid

import pytest

from app.domain.observability import RequestRecord, RequestStatus
from app.models.tenant import TenantORM
from app.models.project import ProjectORM
from app.services.request_log_service import RequestLogService
import app.models  # noqa: F401


@pytest.fixture()
def svc():
    return RequestLogService()


def _seed(session):
    tid, pid = str(uuid.uuid4()), str(uuid.uuid4())
    session.add(TenantORM(tenant_id=tid, name="T", slug=f"t-{tid[:8]}"))
    session.flush()
    session.add(ProjectORM(project_id=pid, tenant_id=tid, name="P"))
    session.flush()
    return tid, pid


def _record(request_id, tid, pid, **kwargs):
    return RequestRecord(
        request_id=request_id, tenant_id=tid, project_id=pid,
        provider=kwargs.get("provider", "openai"),
        model=kwargs.get("model", "gpt-4o"),
        total_tokens=kwargs.get("total_tokens", 100),
        estimated_cost_usd=kwargs.get("estimated_cost_usd", 0.001),
    )


class TestQueryLayer:
    def test_list_recent(self, db_session, svc):
        tid, pid = _seed(db_session)
        for i in range(3):
            svc.persist(db_session, _record(f"req_{i}", tid, pid))
        results = svc.list_recent(db_session)
        assert len(results) == 3

    def test_list_by_project(self, db_session, svc):
        tid, pid = _seed(db_session)
        tid2, pid2 = _seed(db_session)
        svc.persist(db_session, _record("req_a", tid, pid))
        svc.persist(db_session, _record("req_b", tid2, pid2))
        results = svc.list_by_project(db_session, pid)
        assert len(results) == 1
        assert results[0].project_id == pid

    def test_get_by_id(self, db_session, svc):
        tid, pid = _seed(db_session)
        svc.persist(db_session, _record("req_lookup", tid, pid))
        result = svc.get_by_id(db_session, "req_lookup")
        assert result is not None
        assert result.request_id == "req_lookup"

    def test_get_by_id_missing(self, db_session, svc):
        result = svc.get_by_id(db_session, "nonexistent")
        assert result is None

    def test_usage_summary(self, db_session, svc):
        tid, pid = _seed(db_session)
        svc.persist(db_session, _record("r1", tid, pid, total_tokens=100, estimated_cost_usd=0.001))
        svc.persist(db_session, _record("r2", tid, pid, total_tokens=200, estimated_cost_usd=0.002))
        summary = svc.usage_summary_by_project(db_session, pid)
        assert summary["total_requests"] == 2
        assert summary["total_tokens"] == 300
        assert summary["total_estimated_cost_usd"] == 0.003

    def test_limit_respected(self, db_session, svc):
        tid, pid = _seed(db_session)
        for i in range(10):
            svc.persist(db_session, _record(f"req_lim_{i}", tid, pid))
        results = svc.list_recent(db_session, limit=5)
        assert len(results) == 5
