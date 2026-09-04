"""Admin-audit-log aging — the audit trail's own retention.

Historically this module also carried a ``cleanup_request_logs``
hard-deleter that was never scheduled anywhere — a second, unwired
retention mechanism sitting next to the jobs that actually run
(docs/DATA_RETENTION_PLAN.md item 6). That path is gone: request_logs
aging is owned by the DLP stripper + archive mover + partition
machinery, all scheduled through ``app.jobs.retention_pipeline``.

What remains here is the one thing nothing else did: aging the admin
audit log itself. Two rules shape it:

* the audit trail must outlive the data it describes — the default
  window (``audit_log_retention_days``, 365 as of FIX60) holds that
  because an expiry audit row is written AT the moment of deletion and
  then lives its own full year, outlasting the archive row it records
  by 365 days. It is no longer longer than every data window in
  wall-clock terms (the archive default is also 365), so a same-day
  pair now ages out together; raise the window if that margin is ever
  required. The pipeline refuses to run this step at all unless its
  flag is on;
* aging the audit log is itself an audited deletion: a live run
  appends its own record (a fresh row, well inside the window)
  before the pass commits.

Dry-run first, like every deleting step: ``dry_run=True`` (the
pipeline's default) counts and touches nothing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.admin_audit_log import AdminAuditLogORM

import structlog

logger = structlog.get_logger(__name__)

# Default: one year, matching the archive window and the settings
# default. Deletion evidence still outlives the data it describes,
# because the evidence row is created at the moment of deletion and
# then ages on its own clock. The operator can raise it; the pipeline
# flag keeps the step inert until deliberately enabled.
#
# Kept in step with ``settings.audit_log_retention_days`` on purpose:
# this constant is the fallback when the service is constructed
# without one, and a fallback that disagrees with the configured value
# is a second retention policy nobody knows they have.
DEFAULT_AUDIT_LOG_RETENTION_DAYS = 365


class RetentionService:

    def __init__(
        self,
        audit_log_days: int = DEFAULT_AUDIT_LOG_RETENTION_DAYS,
    ) -> None:
        if audit_log_days < 1:
            raise ValueError("audit_log_days must be >= 1")
        self._audit_log_days = audit_log_days

    def cleanup_audit_logs(
        self, session: Session, *, dry_run: bool = True
    ) -> dict:
        """Age out admin audit rows older than the retention window.

        Returns a counts-only dict. ``dry_run=True`` counts and
        deletes nothing. A live pass records ITSELF in the audit log
        before returning, so the aging leaves evidence behind.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._audit_log_days)

        matched = int(
            session.execute(
                select(func.count())
                .select_from(AdminAuditLogORM)
                .where(AdminAuditLogORM.timestamp < cutoff)
            ).scalar()
            or 0
        )

        out = {
            "rows_matched": matched,
            "rows_deleted": 0,
            "dry_run": dry_run,
            "retention_days": self._audit_log_days,
            "cutoff": cutoff.isoformat(),
        }
        if dry_run:
            logger.info("audit_log_aging_dry_run", **out)
            return out

        # Delete + self-record share the caller's transaction: a
        # record failure raises before commit, so an unrecorded aging
        # pass is unrepresentable (nothing was deleted either — the
        # rows are still there for the next attempt).
        result = session.execute(
            delete(AdminAuditLogORM).where(AdminAuditLogORM.timestamp < cutoff)
        )
        out["rows_deleted"] = int(result.rowcount or 0)

        from app.jobs.retention_pipeline import (
            PIPELINE_ACTOR,
            PIPELINE_TENANT,
        )
        from app.services.admin_audit_service import AdminAuditService

        AdminAuditService().record(
            session,
            actor_key_id=PIPELINE_ACTOR,
            tenant_id=PIPELINE_TENANT,
            action="retention_delete",
            resource_type="admin_audit_log",
            resource_id="*",
            details=out,
        )

        logger.warning("audit_log_aging_deleted", **out)
        return out
