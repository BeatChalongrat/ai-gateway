"""Monthly partition-maintenance job for request_logs.

The 041 migration created partitions covering ±3 months from migration
time. As time passes, the future window shrinks; without this job
every write that lands past the existing partitions falls into the
default partition, which:

* defeats the partitioning win (default partition isn't pruned),
* blocks future ATTACH PARTITION calls if its data overlaps the new
  range.

This job runs monthly. For each of the next 3 months, it issues an
idempotent ``CREATE TABLE IF NOT EXISTS ... PARTITION OF ...``. The
migration shape matches the partition naming convention used in 041
so re-runs find existing partitions and no-op.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = structlog.get_logger(__name__)


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


def run_partition_maintenance(session: Session) -> MaintenanceStats:
    """Ensure partitions exist for the next ``_LOOK_AHEAD_MONTHS`` months."""
    now = datetime.utcnow()
    cursor = _month_start(now)
    created: list[str] = []

    for _ in range(_LOOK_AHEAD_MONTHS + 1):  # +1 covers current month
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

    stats = MaintenanceStats(
        partitions_checked=_LOOK_AHEAD_MONTHS + 1,
        partitions_created=len(created),
        created_names=created,
    )
    logger.info("partition_maintenance_complete", **asdict(stats))
    return stats
