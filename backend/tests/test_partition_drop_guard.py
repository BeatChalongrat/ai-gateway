"""With the archive mover off, partition maintenance must never drop data.

The drop path's guard ("only DROP empty partitions") was a comment plus
an untested COUNT — and with ``log_archive_enabled`` now defaulting to
false, expired partitions will routinely still hold rows. These tests
RUN the real job against real Postgres and pin both sides of the guard:

* an expired partition that still has rows is SKIPPED (reported in
  ``skipped_drops``), the table survives, the row survives;
* an expired partition that is genuinely empty IS dropped — proving the
  drop mechanism executes and it is the guard, not inertness, that
  protects the data.

Same lane wiring as ``test_log_archive_job.py``: Postgres-only,
``TEST_POSTGRES_URL`` or loud skip, tagged integration, isolated schema
(the job's parent lookup is schema-scoped via ``current_schema()``).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.integration

_PG_URL = os.environ.get("TEST_POSTGRES_URL", "").strip()

if not _PG_URL:
    pytest.skip(
        "TEST_POSTGRES_URL not set — partition-guard Postgres lane skipped "
        "(set it to a disposable postgres+psycopg URL to run)",
        allow_module_level=True,
    )


@pytest.fixture
def pg_session():
    engine = create_engine(_PG_URL)
    schema = f"partguard_test_{uuid.uuid4().hex[:12]}"
    with engine.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    Session = sessionmaker(bind=engine)
    s = Session()
    s.execute(text(f'SET search_path TO "{schema}"'))
    s.execute(
        text(
            "CREATE TABLE request_logs ("
            " request_id text,"
            " created_at timestamptz NOT NULL"
            ") PARTITION BY RANGE (created_at)"
        )
    )
    # Two long-expired monthly partitions: one holding a row (the
    # archive-is-off world), one empty (the archive-did-its-job world).
    s.execute(
        text(
            "CREATE TABLE request_logs_y2020m01 PARTITION OF request_logs"
            " FOR VALUES FROM ('2020-01-01') TO ('2020-02-01')"
        )
    )
    s.execute(
        text(
            "CREATE TABLE request_logs_y2020m02 PARTITION OF request_logs"
            " FOR VALUES FROM ('2020-02-01') TO ('2020-03-01')"
        )
    )
    s.execute(
        text(
            "INSERT INTO request_logs (request_id, created_at)"
            " VALUES ('survivor-row', '2020-01-15T12:00:00Z')"
        )
    )
    s.commit()
    try:
        yield s
    finally:
        s.rollback()
        s.close()
        with engine.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        engine.dispose()


def test_non_empty_expired_partition_is_skipped_not_dropped(pg_session):
    from app.jobs.partition_maintenance_job import run_partition_maintenance

    stats = run_partition_maintenance(pg_session, retention_days=90)

    # The populated partition was refused…
    assert "request_logs_y2020m01" not in stats.dropped_names
    skipped = {d["partition"]: d for d in stats.skipped_drops}
    assert "request_logs_y2020m01" in skipped
    assert skipped["request_logs_y2020m01"]["reason"] == "not_empty"
    assert skipped["request_logs_y2020m01"]["row_count"] == 1

    # …the table still exists and the row is intact.
    survivors = pg_session.execute(
        text("SELECT request_id FROM request_logs_y2020m01")
    ).scalars().all()
    assert survivors == ["survivor-row"]


def test_empty_expired_partition_is_dropped(pg_session):
    """The guard is a filter, not a disable switch — the empty expired
    sibling really goes, so we know the drop machinery ran."""
    from app.jobs.partition_maintenance_job import run_partition_maintenance

    stats = run_partition_maintenance(pg_session, retention_days=90)

    assert "request_logs_y2020m02" in stats.dropped_names
    gone = pg_session.execute(
        text(
            "SELECT 1 FROM pg_class c"
            " JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE c.relname = 'request_logs_y2020m02'"
            "   AND n.nspname = current_schema()"
        )
    ).first()
    assert gone is None


def test_forward_lookahead_creates_current_and_future_partitions(pg_session):
    from app.jobs.partition_maintenance_job import (
        _partition_name,
        run_partition_maintenance,
    )

    stats = run_partition_maintenance(pg_session, retention_days=90)

    now = datetime.utcnow()
    assert _partition_name(datetime(now.year, now.month, 1)) in (
        stats.created_names
    )
    assert stats.partitions_created >= 2
