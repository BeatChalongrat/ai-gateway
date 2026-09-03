"""FIX50 P1-E (OPEN_DECISIONS #57) — the archive job must actually move rows.

The job's ``INSERT ... SELECT r.*`` copied by position, so the first
migration that widened request_logs without touching the archive twin
broke every run ("INSERT has more expressions than target columns") —
the hot table grew unbounded and the archive stayed empty. Pins:

* named-column copy works (a backdated row moves hot → archive);
* schema drift is refused UP FRONT with the missing columns named —
  never a mid-INSERT SQL error, and never a positional copy again.

Lane wiring: the job is Postgres-only (information_schema, CTE
INSERT...RETURNING), so this follows the
``test_portal_tenant_notnull_postgres`` pattern — set
``TEST_POSTGRES_URL`` to run against a disposable Postgres, otherwise
the module SKIPS LOUDLY. Tagged ``integration`` so the unit lane
(-m "not integration") excludes it either way. The fixture builds
minimal twin tables in an isolated schema; the job's column handling
is generic, which is exactly what these tests exercise.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.integration

_PG_URL = os.environ.get("TEST_POSTGRES_URL", "").strip()

if not _PG_URL:
    pytest.skip(
        "TEST_POSTGRES_URL not set — log-archive Postgres lane skipped "
        "(set it to a disposable postgres+psycopg URL to run)",
        allow_module_level=True,
    )


@pytest.fixture
def pg_session():
    engine = create_engine(_PG_URL)
    schema = f"logarch_test_{uuid.uuid4().hex[:12]}"
    with engine.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    Session = sessionmaker(bind=engine)
    s = Session()
    s.execute(text(f'SET search_path TO "{schema}"'))
    s.execute(text(
        "CREATE TABLE request_logs ("
        " request_id text PRIMARY KEY,"
        " tenant_id text,"
        " created_at timestamptz NOT NULL)"
    ))
    s.execute(text(
        "CREATE TABLE request_logs_archive ("
        " request_id text,"
        " tenant_id text,"
        " created_at timestamptz)"
    ))
    s.commit()
    # The job COMMITs internally, which resets search_path on some pool
    # configs — pin it at the session start of every use.
    s.execute(text(f'SET search_path TO "{schema}"'))
    try:
        yield s
    finally:
        s.rollback()
        s.close()
        with engine.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        engine.dispose()


def _seed_old(session, rid: str):
    session.execute(
        text(
            "INSERT INTO request_logs (request_id, tenant_id, created_at) "
            "VALUES (:r, 't1', :ts)"
        ),
        {"r": rid, "ts": datetime.now(timezone.utc) - timedelta(days=120)},
    )
    session.commit()


def test_archive_moves_old_rows_with_named_columns(pg_session):
    from app.jobs.log_archive_job import run_log_archive

    rid = f"arch-{uuid.uuid4().hex[:12]}"
    _seed_old(pg_session, rid)

    stats = run_log_archive(pg_session, archive_days=90)

    assert stats.schema_drift == ()
    assert stats.rows_archived == 1
    assert pg_session.execute(
        text("SELECT count(*) FROM request_logs_archive WHERE request_id=:r"),
        {"r": rid},
    ).scalar() == 1
    assert pg_session.execute(
        text("SELECT count(*) FROM request_logs WHERE request_id=:r"),
        {"r": rid},
    ).scalar() == 0


def test_archive_refuses_on_schema_drift_before_touching_rows(pg_session):
    from app.jobs.log_archive_job import run_log_archive

    rid = f"arch-{uuid.uuid4().hex[:12]}"
    _seed_old(pg_session, rid)
    # Widen the hot table only — the exact shape that silently broke the
    # job for months (migrations 062/079 + the reasoning columns).
    pg_session.execute(
        text("ALTER TABLE request_logs ADD COLUMN drift_probe integer")
    )
    pg_session.commit()

    stats = run_log_archive(pg_session, archive_days=90)

    assert stats.rows_archived == 0
    assert "drift_probe" in stats.schema_drift
    # The eligible row is still in the hot table — nothing was deleted.
    assert pg_session.execute(
        text("SELECT count(*) FROM request_logs WHERE request_id=:r"),
        {"r": rid},
    ).scalar() == 1
