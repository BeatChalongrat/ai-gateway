"""Cold-storage archive job for request_logs.

Runs daily alongside :mod:`app.jobs.dlp_retention`. Rows older than
``settings.log_archive_days`` (default 90) are copied to
``request_logs_archive`` and deleted from the partitioned
``request_logs`` table.

Trade-offs:

* The hot table only ever holds ~90 days of traffic, keeping every
  index small enough to fit in shared_buffers on a normal Postgres
  pod. Dashboards stay fast.
* The archive table never has rows deleted — it's the source of truth
  for compliance / billing reconciliation; queries against it are
  rare and expected to be slow.

Atomicity:

* The copy + delete runs in batches inside a single transaction per
  batch. A crash between batches leaves the archive consistent (no
  partial row), only with fewer rows than the source — the next run
  picks up where this one left off.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings

logger = structlog.get_logger(__name__)


# Tunable. 5_000 rows per round trip is a comfortable size for a
# partitioned table: small enough that a single COMMIT is fast, large
# enough that we don't spend more time on round trips than the work.
_BATCH_SIZE = 5_000


@dataclass(frozen=True)
class ArchiveStats:
    """Outcome of one archive pass."""

    rows_archived: int
    archive_days: int
    cutoff: str
    schema_drift: tuple[str, ...] = ()


_COLUMNS_SQL = text(
    """
    SELECT column_name
    FROM information_schema.columns
    WHERE table_schema = current_schema() AND table_name = :table
    ORDER BY ordinal_position
    """
)


def _table_columns(session: Session, table: str) -> list[str]:
    return [row[0] for row in session.execute(_COLUMNS_SQL, {"table": table})]


def run_log_archive(
    session: Session,
    *,
    archive_days: int | None = None,
) -> ArchiveStats:
    """Archive request_logs rows older than the archive window.

    Returns the count of rows archived. The job is idempotent — a
    second run on the same table is a no-op because the WHERE clause
    only catches rows still in the hot table.

    FIX50 P1-E (OPEN_DECISIONS #57): the INSERT names its columns
    instead of ``SELECT r.*`` — positional copy broke the first time a
    migration added a column to the hot table only, and had failed
    every run since (the hot table grew unbounded, the archive never
    received a row). If the hot table has columns the archive lacks,
    the run REFUSES up front: archiving only the common columns would
    silently drop the drifted columns' data, because the hot row is
    deleted after the copy. The error names the columns so the fix is
    a one-line migration (see 081 for the shape).
    """
    days = archive_days if archive_days is not None else settings.log_archive_days
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    hot_cols = _table_columns(session, "request_logs")
    archive_cols = set(_table_columns(session, "request_logs_archive"))
    missing = tuple(c for c in hot_cols if c not in archive_cols)
    if missing:
        logger.error(
            "log_archive_schema_drift",
            missing_in_archive=list(missing),
            hint="add the columns to request_logs_archive (migration 081 pattern); the job will not move rows until the schemas agree",
        )
        return ArchiveStats(
            rows_archived=0,
            archive_days=days,
            cutoff=cutoff.isoformat(),
            schema_drift=missing,
        )
    col_list = ", ".join(hot_cols)
    select_list = ", ".join(f"r.{c}" for c in hot_cols)

    total = 0
    while True:
        # Copy a batch into the archive. CTE + RETURNING keeps the
        # delete tightly scoped to exactly the rows we just copied —
        # no risk of deleting rows that arrived between the copy and
        # the delete (which would happen with a naive
        # ``DELETE WHERE created_at < cutoff`` + LIMIT).
        result = session.execute(
            text(
                f"""
                WITH moved AS (
                    SELECT request_id
                    FROM request_logs
                    WHERE created_at < :cutoff
                    ORDER BY created_at
                    LIMIT :batch
                ),
                copied AS (
                    INSERT INTO request_logs_archive ({col_list})
                    SELECT {select_list}
                    FROM request_logs r
                    JOIN moved m USING (request_id)
                    RETURNING request_id
                )
                DELETE FROM request_logs
                WHERE request_id IN (SELECT request_id FROM copied)
                """
            ),
            {"cutoff": cutoff, "batch": _BATCH_SIZE},
        )
        batch_rows = result.rowcount or 0
        session.commit()
        total += batch_rows
        if batch_rows < _BATCH_SIZE:
            break

    stats = ArchiveStats(
        rows_archived=total,
        archive_days=days,
        cutoff=cutoff.isoformat(),
    )
    logger.info("log_archive_job_complete", **asdict(stats))
    return stats
