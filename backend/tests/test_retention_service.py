"""P10-05: Retention / archival policy tests."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.domain.observability import RequestRecord
from app.models.admin_audit_log import AdminAuditLogORM
from app.models.tenant import TenantORM
from app.models.project import ProjectORM
from app.services.admin_audit_service import AdminAuditService
from app.services.request_log_service import RequestLogService
from app.services.retention_service import RetentionService
import app.models  # noqa: F401


def _seed(session):
    tid, pid = str(uuid.uuid4()), str(uuid.uuid4())
    session.add(TenantORM(tenant_id=tid, name="T", slug=f"t-{tid[:8]}"))
    session.flush()
    session.add(ProjectORM(project_id=pid, tenant_id=tid, name="P"))
    session.flush()
    return tid, pid


class TestRetentionCleanup:
    def test_recent_logs_not_deleted(self, db_session):
        tid, pid = _seed(db_session)
        RequestLogService().persist(db_session, RequestRecord(
            request_id="recent", tenant_id=tid, project_id=pid,
        ))
        svc = RetentionService(request_log_days=90)
        deleted = svc.cleanup_request_logs(db_session)
        assert deleted == 0

    def test_old_logs_deleted(self, db_session):
        tid, pid = _seed(db_session)
        RequestLogService().persist(db_session, RequestRecord(
            request_id="old", tenant_id=tid, project_id=pid,
        ))
        # Manually backdate the record
        from app.models.request_log import RequestLogORM
        log = db_session.get(RequestLogORM, "old")
        log.created_at = datetime.now(timezone.utc) - timedelta(days=100)
        db_session.flush()

        svc = RetentionService(request_log_days=90)
        deleted = svc.cleanup_request_logs(db_session)
        assert deleted == 1

    def test_audit_retention(self, db_session):
        AdminAuditService().record(
            db_session, actor_key_id="k1", tenant_id="t1",
            action="test", resource_type="x", resource_id="y",
        )
        # Backdate
        entry = db_session.execute(
            text("SELECT id FROM admin_audit_log LIMIT 1")
        ).scalar()
        db_session.execute(
            text(f"UPDATE admin_audit_log SET timestamp = :ts WHERE id = :id"),
            {"ts": datetime.now(timezone.utc) - timedelta(days=400), "id": entry},
        )
        db_session.flush()

        svc = RetentionService(audit_log_days=365)
        deleted = svc.cleanup_audit_logs(db_session)
        assert deleted == 1

    def test_run_all(self, db_session):
        svc = RetentionService()
        result = svc.run_all(db_session)
        assert "request_logs_deleted" in result
        assert "audit_logs_deleted" in result

    def test_configurable_retention(self):
        svc = RetentionService(request_log_days=30, audit_log_days=180)
        assert svc._request_log_days == 30
        assert svc._audit_log_days == 180
