"""Data retention and cleanup service.

Enforces retention policies by deleting records older than configured
thresholds. Designed to run as a background task or scheduled job.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.request_log import RequestLogORM
from app.models.admin_audit_log import AdminAuditLogORM

import structlog

logger = structlog.get_logger(__name__)

# Default retention periods (days)
DEFAULT_REQUEST_LOG_RETENTION_DAYS = 90
DEFAULT_AUDIT_LOG_RETENTION_DAYS = 365


class RetentionService:

    def __init__(
        self,
        request_log_days: int = DEFAULT_REQUEST_LOG_RETENTION_DAYS,
        audit_log_days: int = DEFAULT_AUDIT_LOG_RETENTION_DAYS,
    ) -> None:
        self._request_log_days = request_log_days
        self._audit_log_days = audit_log_days

    def cleanup_request_logs(self, session: Session) -> int:
        """Delete request logs older than retention period. Returns count deleted."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._request_log_days)
        stmt = delete(RequestLogORM).where(RequestLogORM.created_at < cutoff)
        result = session.execute(stmt)
        count = result.rowcount
        if count:
            logger.info(
                "retention_cleanup",
                table="request_logs",
                deleted=count,
                cutoff_days=self._request_log_days,
            )
        return count

    def cleanup_audit_logs(self, session: Session) -> int:
        """Delete admin audit logs older than retention period."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._audit_log_days)
        stmt = delete(AdminAuditLogORM).where(AdminAuditLogORM.timestamp < cutoff)
        result = session.execute(stmt)
        count = result.rowcount
        if count:
            logger.info(
                "retention_cleanup",
                table="admin_audit_log",
                deleted=count,
                cutoff_days=self._audit_log_days,
            )
        return count

    def run_all(self, session: Session) -> dict:
        """Run all cleanup tasks. Returns summary."""
        return {
            "request_logs_deleted": self.cleanup_request_logs(session),
            "audit_logs_deleted": self.cleanup_audit_logs(session),
        }
