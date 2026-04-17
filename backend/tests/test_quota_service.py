"""P8-04: Quota enforcement tests."""

import uuid
import pytest

from app.core.exceptions import QuotaExceededError
from app.domain.observability import RequestRecord
from app.models.project import ProjectORM
from app.models.tenant import TenantORM
from app.services.quota_service import QuotaService
from app.services.request_log_service import RequestLogService
import app.models  # noqa: F401


@pytest.fixture()
def svc():
    return QuotaService()


def _seed(session, max_requests=None):
    tid, pid = str(uuid.uuid4()), str(uuid.uuid4())
    session.add(TenantORM(tenant_id=tid, name="T", slug=f"t-{tid[:8]}"))
    session.flush()
    session.add(ProjectORM(
        project_id=pid, tenant_id=tid, name="P",
        max_requests_per_day=max_requests,
    ))
    session.flush()
    return tid, pid


class TestQuotaEnforcement:
    def test_no_quota_passes(self, db_session, svc):
        _, pid = _seed(db_session, max_requests=None)
        svc.check_request_quota(db_session, pid)  # should not raise

    def test_under_quota_passes(self, db_session, svc):
        tid, pid = _seed(db_session, max_requests=5)
        log_svc = RequestLogService()
        log_svc.persist(db_session, RequestRecord(
            request_id="r1", tenant_id=tid, project_id=pid,
        ))
        svc.check_request_quota(db_session, pid)  # 1 < 5, should pass

    def test_at_quota_raises(self, db_session, svc):
        tid, pid = _seed(db_session, max_requests=2)
        log_svc = RequestLogService()
        for i in range(2):
            log_svc.persist(db_session, RequestRecord(
                request_id=f"r_{i}", tenant_id=tid, project_id=pid,
            ))
        with pytest.raises(QuotaExceededError) as exc_info:
            svc.check_request_quota(db_session, pid)
        assert exc_info.value.details["limit"] == 2
        assert exc_info.value.details["used"] == 2

    def test_missing_project_passes(self, db_session, svc):
        svc.check_request_quota(db_session, "nonexistent")  # should not raise

    def test_quota_details_in_error(self, db_session, svc):
        tid, pid = _seed(db_session, max_requests=1)
        log_svc = RequestLogService()
        log_svc.persist(db_session, RequestRecord(
            request_id="r_x", tenant_id=tid, project_id=pid,
        ))
        with pytest.raises(QuotaExceededError) as exc_info:
            svc.check_request_quota(db_session, pid)
        assert "project_id" in exc_info.value.details
