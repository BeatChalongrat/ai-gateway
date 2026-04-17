"""P6-07: Request log persistence tests."""

import uuid

import pytest

from app.domain.observability import RequestRecord, RequestStatus
from app.models.request_log import RequestLogORM
from app.services.request_log_service import RequestLogService
import app.models  # noqa: F401


@pytest.fixture()
def svc():
    return RequestLogService()


def _seed_tenant_project(session):
    from app.models.tenant import TenantORM
    from app.models.project import ProjectORM
    tid, pid = str(uuid.uuid4()), str(uuid.uuid4())
    session.add(TenantORM(tenant_id=tid, name="T", slug=f"t-{tid[:8]}"))
    session.flush()
    session.add(ProjectORM(project_id=pid, tenant_id=tid, name="P"))
    session.flush()
    return tid, pid


class TestRequestLogPersistence:
    def test_persist_and_read_back(self, db_session, svc):
        tid, pid = _seed_tenant_project(db_session)
        record = RequestRecord(
            request_id="req_test1",
            tenant_id=tid,
            project_id=pid,
            provider="openai",
            model="gpt-4o",
            status=RequestStatus.SUCCESS,
            policy_action="allow",
            latency_ms=150,
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            estimated_cost_usd=0.001,
        )
        svc.persist(db_session, record)

        result = db_session.get(RequestLogORM, "req_test1")
        assert result is not None
        assert result.provider == "openai"
        assert result.latency_ms == 150
        assert result.total_tokens == 15
        assert result.status == "success"

    def test_persist_error_record(self, db_session, svc):
        tid, pid = _seed_tenant_project(db_session)
        record = RequestRecord(
            request_id="req_err1",
            tenant_id=tid,
            project_id=pid,
            status=RequestStatus.PROVIDER_ERROR,
            error_code="provider_timeout",
        )
        svc.persist(db_session, record)

        result = db_session.get(RequestLogORM, "req_err1")
        assert result.status == "provider_error"
        assert result.error_code == "provider_timeout"

    def test_persist_blocked_record(self, db_session, svc):
        tid, pid = _seed_tenant_project(db_session)
        record = RequestRecord(
            request_id="req_block1",
            tenant_id=tid,
            project_id=pid,
            status=RequestStatus.BLOCKED,
            policy_action="block",
        )
        svc.persist(db_session, record)

        result = db_session.get(RequestLogORM, "req_block1")
        assert result.status == "blocked"
        assert result.policy_action == "block"

    def test_columns_match_orm(self, db_inspector):
        cols = {c["name"] for c in db_inspector.get_columns("request_logs")}
        assert "prompt_tokens" in cols
        assert "completion_tokens" in cols
        assert "total_tokens" in cols
        assert "error_code" in cols
