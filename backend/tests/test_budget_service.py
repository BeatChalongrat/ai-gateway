"""P8-05: Budget cap enforcement tests."""

import uuid
import pytest

from app.core.exceptions import QuotaExceededError
from app.domain.observability import RequestRecord
from app.models.project import ProjectORM
from app.models.tenant import TenantORM
from app.services.budget_service import BudgetService
from app.services.request_log_service import RequestLogService
import app.models  # noqa: F401


@pytest.fixture()
def svc():
    return BudgetService()


def _seed(session, budget_limit=None):
    tid, pid = str(uuid.uuid4()), str(uuid.uuid4())
    session.add(TenantORM(tenant_id=tid, name="T", slug=f"t-{tid[:8]}"))
    session.flush()
    session.add(ProjectORM(project_id=pid, tenant_id=tid, name="P", budget_limit=budget_limit))
    session.flush()
    return tid, pid


class TestBudgetEnforcement:
    def test_no_budget_passes(self, db_session, svc):
        _, pid = _seed(db_session, budget_limit=None)
        svc.check_budget(db_session, pid)

    def test_under_budget_passes(self, db_session, svc):
        tid, pid = _seed(db_session, budget_limit=1.0)
        RequestLogService().persist(db_session, RequestRecord(
            request_id="r1", tenant_id=tid, project_id=pid, estimated_cost_usd=0.5,
        ))
        svc.check_budget(db_session, pid)

    def test_over_budget_raises(self, db_session, svc):
        tid, pid = _seed(db_session, budget_limit=0.01)
        RequestLogService().persist(db_session, RequestRecord(
            request_id="r1", tenant_id=tid, project_id=pid, estimated_cost_usd=0.02,
        ))
        with pytest.raises(QuotaExceededError) as exc_info:
            svc.check_budget(db_session, pid)
        assert "budget" in exc_info.value.message.lower()
        assert exc_info.value.details["is_estimate"] is True

    def test_missing_project_passes(self, db_session, svc):
        svc.check_budget(db_session, "nonexistent")
