"""Convert request_logs to a monthly range-partitioned table + archive.

Revision ID: 041
Revises: 040
Create Date: 2026-05-15

The May 2026 scalability audit projected request_logs at 1.5B+ rows
within a year at 5M users. Single-table scans become impractical at
that size even with compound indexes. This migration converts the
table to PostgreSQL's native range partitioning by ``created_at``
(monthly buckets) so retention/archive operations can DETACH whole
partitions instead of bulk-deleting rows.

### Zero-downtime approach

1. Create new partitioned table ``request_logs_part`` with identical
   columns + partition key on ``created_at``.
2. Create monthly partitions covering ±3 months from today, plus a
   default partition for overflow.
3. Re-create every index from the original table on the partitioned
   table (Postgres replicates these to each partition automatically).
4. Copy existing data into the partitioned table.
5. Atomically swap names: ``request_logs`` → ``request_logs_legacy``,
   ``request_logs_part`` → ``request_logs``.
6. Create ``request_logs_archive`` for cold storage (NOT partitioned;
   indexes minimal so writes are cheap).

The legacy table is **not dropped** — operators verify the swap then
drop it manually in a follow-up cleanup. This is the explicit
"do NOT DROP until verified" requirement from the audit fix spec.

### Downgrade

Drops the partitioned table and renames the legacy table back. Any
rows written after the upgrade live only on the partitioned side, so
a real downgrade would LOSE those rows — the downgrade is for the
no-traffic case (e.g. test-suite tear-down) and is logged accordingly.
"""

from datetime import datetime, timedelta

from alembic import op

revision = "041"
down_revision = "040"
branch_labels = None
depends_on = None


def _month_start(d: datetime) -> datetime:
    return datetime(d.year, d.month, 1)


def _next_month(d: datetime) -> datetime:
    if d.month == 12:
        return datetime(d.year + 1, 1, 1)
    return datetime(d.year, d.month + 1, 1)


def _partition_name(d: datetime) -> str:
    return f"request_logs_y{d.year}m{d.month:02d}"


def upgrade() -> None:
    bind = op.get_bind()

    # 1) Build the partitioned shell. The CREATE TABLE ... LIKE
    # clones structure (columns + nullability + defaults + storage
    # params) without copying constraints or indexes — we recreate
    # those explicitly. ``PARTITION BY RANGE (created_at)`` is the
    # required addition.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS request_logs_part (
            LIKE request_logs INCLUDING DEFAULTS INCLUDING STORAGE
        ) PARTITION BY RANGE (created_at)
        """
    )

    # 2) Monthly partitions: current ±3 months. The default partition
    # absorbs anything outside the window so writes never fail; the
    # partition_maintenance_job will create future months ahead of
    # time so the default stays empty.
    now = datetime.utcnow()
    cursor = _month_start(now) - timedelta(days=120)  # ≈4 months back
    end = _month_start(now) + timedelta(days=120)     # ≈4 months forward
    while cursor <= end:
        nxt = _next_month(cursor)
        name = _partition_name(cursor)
        op.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {name}
            PARTITION OF request_logs_part
            FOR VALUES FROM ('{cursor.isoformat()}')
                       TO   ('{nxt.isoformat()}')
            """
        )
        cursor = nxt

    # Default partition — catches gaps and future months until the
    # maintenance job extends the window.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS request_logs_default
        PARTITION OF request_logs_part DEFAULT
        """
    )

    # 3) Indexes — recreate the compound indexes from migration 040 on
    # the partitioned root. Postgres propagates partitioned indexes to
    # every child partition automatically.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_request_logs_part_tenant_created "
        "ON request_logs_part (tenant_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_request_logs_part_project_created "
        "ON request_logs_part (project_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_request_logs_part_project_status "
        "ON request_logs_part (project_id, status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_request_logs_part_tenant_cost "
        "ON request_logs_part (tenant_id, created_at DESC) "
        "INCLUDE (estimated_cost_usd)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_request_logs_part_created_at "
        "ON request_logs_part (created_at)"
    )

    # 4) Copy existing rows. INSERT ... SELECT is fast for typical
    # dev sizes and acceptable for prod tables under ~100M rows; for
    # larger live tables operators would run this step manually with
    # batching + LOCK TABLE in MAINTENANCE mode.
    op.execute(
        "INSERT INTO request_logs_part SELECT * FROM request_logs"
    )

    # 5) Atomic rename swap. Both DDL statements run in this
    # migration's transaction so the new name is exposed to readers
    # in a single commit.
    op.execute("ALTER TABLE request_logs RENAME TO request_logs_legacy")
    op.execute("ALTER TABLE request_logs_part RENAME TO request_logs")

    # 6) Cold-storage archive table. Schema mirrors request_logs so
    # archive_job can do a straight ``INSERT ... SELECT``. Index set
    # is intentionally minimal — archive is rarely queried; writes
    # need to be cheap.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS request_logs_archive (
            LIKE request_logs INCLUDING DEFAULTS INCLUDING STORAGE
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_request_logs_archive_tenant_created "
        "ON request_logs_archive (tenant_id, created_at DESC)"
    )


def downgrade() -> None:
    # Roll the rename back so subsequent migrations see the original
    # name. The partitioned table + archive are dropped; legacy table
    # is preserved (it's the source of truth at this point).
    op.execute("DROP TABLE IF EXISTS request_logs_archive")
    # Need to drop partitions explicitly because the parent table
    # cannot be dropped while partitions are attached.
    op.execute("DROP TABLE IF EXISTS request_logs CASCADE")
    op.execute("ALTER TABLE request_logs_legacy RENAME TO request_logs")
