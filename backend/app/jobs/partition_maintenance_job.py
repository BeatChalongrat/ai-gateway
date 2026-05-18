"""Daily partition maintenance for ``request_logs``.

Two responsibilities:

1. **Look ahead** — make sure the *current* month plus the next
   :data:`_LOOK_AHEAD_MONTHS` (default 3) months each have a partition.
   The 041 migration only seeded ±3 months from migration time, so
   without this job every write past the seeded window lands in
   ``request_logs_default`` — which never gets pruned and defeats
   the partitioning win.

2. **Drop old** — DETACH and DROP partitions whose upper bound is
   strictly older than ``settings.dlp_raw_retention_days`` (default
   90). By that point ``log_archive_job`` has already copied each
   row into ``request_logs_archive`` and deleted it from the hot
   table, so the partition is empty metadata and dropping it is a
   trivial DDL operation. The DROP is guarded — we only DROP a
   partition that has zero rows; if a slow archive run left rows
   behind we log a warning and skip the drop on this pass.

Idempotent + safe to re-run.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings

logger = structlog.get_logger(__name__)


# Look ahead 3 months so a slipped run still has 2 months of runway —
# the spec asks for 2-month coverage and the +1 is the safety margin
# operators rely on when a daily run is missed.
_LOOK_AHEAD_MONTHS = 3


def _month_start(d: datetime) -> datetime:
    return datetime(d.year, d.month, 1)


def _next_month(d: datetime) -> datetime:
    if d.month == 12:
        return datetime(d.year + 1, 1, 1)
    return datetime(d.year, d.month + 1, 1)


def _partition_name(d: datetime) -> str:
    return f"request_logs_y{d.year}m{d.month:02d}"


@dataclass(frozen=True)
class MaintenanceStats:
    partitions_checked: int
    partitions_created: int
    created_names: list[str]
    partitions_dropped: int = 0
    dropped_names: list[str] = field(default_factory=list)
    skipped_drops: list[dict] = field(default_factory=list)


def run_partition_maintenance(
    session: Session,
    *,
    retention_days: int | None = None,
) -> MaintenanceStats:
    """Ensure forward partitions exist and drop expired empty ones.

    ``retention_days`` defaults to ``settings.dlp_raw_retention_days``.
    Pass an explicit value from tests to exercise the drop path
    without waiting for real calendar time to pass.
    """
    now = datetime.utcnow()
    cursor = _month_start(now)
    created: list[str] = []

    # ---- 1. Forward look-ahead ----------------------------------------
    for _ in range(_LOOK_AHEAD_MONTHS + 1):  # +1 covers the current month
        nxt = _next_month(cursor)
        name = _partition_name(cursor)
        exists = session.execute(
            text(
                "SELECT 1 FROM pg_class WHERE relname = :name LIMIT 1"
            ),
            {"name": name},
        ).first()
        if exists is None:
            session.execute(
                text(
                    f"""
                    CREATE TABLE {name}
                    PARTITION OF request_logs
                    FOR VALUES FROM ('{cursor.isoformat()}')
                               TO   ('{nxt.isoformat()}')
                    """
                )
            )
            session.commit()
            created.append(name)
            logger.info(
                "partition_created",
                partition=name,
                from_ts=cursor.isoformat(),
                to_ts=nxt.isoformat(),
            )
        cursor = nxt

    # ---- 2. Drop expired empty partitions -----------------------------
    days = (
        retention_days
        if retention_days is not None
        else settings.dlp_raw_retention_days
    )
    cutoff = datetime.utcnow() - timedelta(days=days)

    # List every child of the request_logs partitioned root that looks
    # like one of our monthly partitions (the regex matches the
    # ``request_logs_y{YYYY}m{MM}`` naming convention from migration
    # 041). ``request_logs_default`` is intentionally excluded — it
    # absorbs out-of-range writes and is never time-bound.
    children = session.execute(
        text(
            """
            SELECT c.relname AS partition_name,
                   pg_get_expr(c.relpartbound, c.oid) AS bound_expr
            FROM pg_inherits i
            JOIN pg_class c ON c.oid = i.inhrelid
            JOIN pg_class p ON p.oid = i.inhparent
            WHERE p.relname = 'request_logs'
              AND c.relname ~ '^request_logs_y[0-9]{4}m[0-9]{2}$'
            ORDER BY c.relname
            """
        )
    ).all()

    dropped: list[str] = []
    skipped: list[dict] = []
    for row in children:
        name = row.partition_name
        # Parse "y2026m05" out of the name. Cheaper + more robust than
        # parsing the FOR VALUES expression which is locale-formatted.
        try:
            year = int(name[len("request_logs_y"): len("request_logs_y") + 4])
            month = int(name[-2:])
            partition_upper = _next_month(datetime(year, month, 1))
        except (ValueError, IndexError):
            continue

        if partition_upper >= cutoff:
            continue  # still within retention window

        # Only DROP empty partitions — if log_archive_job slipped we'd
        # rather keep the row than lose it silently.
        row_count = session.execute(
            text(f"SELECT COUNT(*) FROM {name}")
        ).scalar() or 0
        if row_count > 0:
            skipped.append(
                {
                    "partition": name,
                    "row_count": int(row_count),
                    "reason": "not_empty",
                }
            )
            logger.warning(
                "partition_drop_skipped",
                partition=name,
                row_count=int(row_count),
                upper_bound=partition_upper.isoformat(),
            )
            continue

        # Detach first so the DROP doesn't lock the parent any longer
        # than necessary. CONCURRENTLY isn't available inside a
        # transaction block so we keep a normal DETACH; the partition
        # being empty means the lock window is tiny.
        session.execute(
            text(f"ALTER TABLE request_logs DETACH PARTITION {name}")
        )
        session.execute(text(f"DROP TABLE {name}"))
        session.commit()
        dropped.append(name)
        logger.info(
            "partition_dropped",
            partition=name,
            upper_bound=partition_upper.isoformat(),
        )

    stats = MaintenanceStats(
        partitions_checked=_LOOK_AHEAD_MONTHS + 1,
        partitions_created=len(created),
        created_names=created,
        partitions_dropped=len(dropped),
        dropped_names=dropped,
        skipped_drops=skipped,
    )
    logger.info("partition_maintenance_complete", **asdict(stats))
    return stats


# ---------------------------------------------------------------------------
# Read-only inspection helper — backs GET /admin/dashboard/partition-health.
# ---------------------------------------------------------------------------

def list_partition_health(session: Session) -> dict:
    """Return a human-readable snapshot of the partition layout.

    Returns ``{"supported": False, ...}`` on non-Postgres engines so
    callers (tests, SQLite dev) can degrade gracefully. On Postgres,
    returns a dict with:

      * ``partitions``       — list of ``{name, row_estimate, bounds}``
      * ``oldest_partition`` — name of the chronologically lowest child
      * ``newest_partition`` — name of the chronologically highest child
      * ``next_month_missing`` — bool, True if the next-month partition
        isn't present (operator should expect a default-partition
        spillover until the maintenance job catches up).
    """
    dialect = session.get_bind().dialect.name
    if dialect != "postgresql":
        return {
            "supported": False,
            "engine": dialect,
            "message": (
                "Partition introspection is Postgres-only; "
                "this is a degraded response on a non-PG engine."
            ),
        }

    rows = session.execute(
        text(
            """
            SELECT c.relname AS partition_name,
                   pg_get_expr(c.relpartbound, c.oid) AS bounds,
                   c.reltuples AS row_estimate
            FROM pg_inherits i
            JOIN pg_class c ON c.oid = i.inhrelid
            JOIN pg_class p ON p.oid = i.inhparent
            WHERE p.relname = 'request_logs'
            ORDER BY c.relname
            """
        )
    ).all()

    partitions = [
        {
            "name": r.partition_name,
            "bounds": r.bounds or "DEFAULT",
            "row_estimate": int(r.row_estimate or 0),
        }
        for r in rows
    ]

    # Filter to the date-named children to pick oldest/newest. The
    # default partition sorts at the top alphabetically, which would
    # otherwise pollute the oldest pointer.
    dated = [p for p in partitions if p["name"].startswith("request_logs_y")]
    oldest = dated[0]["name"] if dated else None
    newest = dated[-1]["name"] if dated else None

    now = datetime.now(timezone.utc)
    next_month = _next_month(_month_start(now.replace(tzinfo=None)))
    next_month_name = _partition_name(next_month)
    next_month_missing = next_month_name not in {p["name"] for p in partitions}

    return {
        "supported": True,
        "engine": dialect,
        "partitions": partitions,
        "oldest_partition": oldest,
        "newest_partition": newest,
        "next_month_expected": next_month_name,
        "next_month_missing": next_month_missing,
    }
