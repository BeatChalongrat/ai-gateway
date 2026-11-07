"""Archive expiry — ``request_logs_archive`` finally has a way out.

The archive was write-only: rows moved in and nothing ever removed
them (docs/DATA_RETENTION_PLAN.md item 2). This job deletes archive
rows older than ``archive_retention_days``, under the mandatory
safety shape for anything that deletes:

* it runs only when ``archive_expiry_enabled`` is true (default off),
* ``dry_run`` (default on) COUNTS the matching rows and touches
  nothing — the operator reads the number before flipping it off,
* every LIVE run writes an admin-audit row (actor
  ``system:retention-pipeline``): rule, cutoff, retention window, and
  the deleted count — counts only, never row content.

Scheduling and ordering live in ``retention_pipeline`` (this step
runs after the stripper and the mover, at 00:00 UTC).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ExpiryStats:
    """Counts-only outcome of one expiry pass."""

    rows_matched: int
    rows_deleted: int
    dry_run: bool
    retention_days: int
    cutoff: str

    def to_dict(self) -> dict:
        return asdict(self)


def run_archive_expiry(
    session: Session,
    *,
    retention_days: int,
    dry_run: bool = True,
) -> ExpiryStats:
    """Delete (or, in dry-run, only count) expired archive rows."""
    if retention_days < 1:
        raise ValueError("archive_retention_days must be >= 1")
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

    matched = int(
        session.execute(
            text(
                "SELECT count(*) FROM request_logs_archive"
                " WHERE created_at < :cutoff"
            ),
            {"cutoff": cutoff},
        ).scalar()
        or 0
    )

    if dry_run:
        stats = ExpiryStats(
            rows_matched=matched,
            rows_deleted=0,
            dry_run=True,
            retention_days=retention_days,
            cutoff=cutoff.isoformat(),
        )
        logger.info("archive_expiry_dry_run", **stats.to_dict())
        return stats

    # Delete + audit record commit ATOMICALLY: a deletion without its
    # record is unrepresentable (same contract as the chat-edge
    # cascade). If the audit write fails, the whole transaction rolls
    # back — nothing was lost, the error surfaces, the rows are still
    # there for the next attempt.
    try:
        deleted = session.execute(
            text(
                "DELETE FROM request_logs_archive WHERE created_at < :cutoff"
            ),
            {"cutoff": cutoff},
        ).rowcount

        stats = ExpiryStats(
            rows_matched=matched,
            rows_deleted=int(deleted or 0),
            dry_run=False,
            retention_days=retention_days,
            cutoff=cutoff.isoformat(),
        )

        from app.jobs.retention_pipeline import PIPELINE_ACTOR, PIPELINE_TENANT
        from app.services.admin_audit_service import AdminAuditService

        AdminAuditService().record(
            session,
            actor_key_id=PIPELINE_ACTOR,
            tenant_id=PIPELINE_TENANT,
            action="retention_delete",
            resource_type="request_logs_archive",
            resource_id="*",
            details=stats.to_dict(),
        )
        session.commit()
    except Exception:
        session.rollback()
        raise

    logger.warning("archive_expiry_deleted", **stats.to_dict())
    return stats
