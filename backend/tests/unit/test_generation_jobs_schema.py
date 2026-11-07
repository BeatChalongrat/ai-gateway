"""Schema tests for the ``generation_jobs`` table (migration 056).

Unit lane: builds the table from ORM metadata on the SQLite test engine
(the same way ``conftest.sqlite_engine`` does) and asserts the column
shape, server defaults, the partial-unique idempotency index, and the
worker scan index. The true alembic forward/back run against a real
Postgres schema is exercised by the ``integration`` lane
(``test_migrations``) — the migration chain includes Postgres-only ops
(e.g. 041 partitioning) so it cannot run on SQLite here.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.generation_job import GenerationJobORM
from app.models.portal_user import PortalUserORM
from app.models.tenant import TenantORM


def _ttl():
    return datetime.now(timezone.utc) + timedelta(days=7)


def _make_parents(session):
    session.add(TenantORM(tenant_id="t-1", name="T1", slug="t-1"))
    session.add(
        PortalUserORM(
            id="u-1", email="u1@example.com", password_hash="x",
            tenant_id="t-1",  # NOT NULL since SSO Phase 1 A1
        )
    )
    session.flush()


def _job(**over):
    base = dict(
        id=str(uuid.uuid4()),
        tenant_id="t-1",
        user_id="u-1",
        job_type="video",
        provider="replicate",
        model_id="kwaivgi/kling-v2.5",
        input_prompt="a cat surfing a wave",
        estimated_cost_credits=1000,
        ttl_expires_at=_ttl(),
    )
    base.update(over)
    return GenerationJobORM(**base)


def test_insert_minimal_job(db_session):
    """A row with only the required fields persists; server defaults fill in."""
    _make_parents(db_session)
    job = _job()
    db_session.add(job)
    db_session.flush()
    # Force a reload so DB-side server defaults populate the ORM object.
    db_session.expire(job)

    row = db_session.query(GenerationJobORM).one()
    assert row.status == "queued"          # server_default 'queued'
    assert row.input_metadata == {}        # server_default '{}'
    assert row.progress_pct is None
    assert row.external_job_id is None
    assert row.actual_cost_credits is None
    assert row.estimated_cost_credits == 1000
    assert row.job_type == "video"


def test_unique_idempotency_key_rejects_duplicate(db_session):
    """Two jobs with the same (tenant_id, idempotency_key) collide."""
    _make_parents(db_session)
    db_session.add(_job(idempotency_key="idem-abc"))
    db_session.flush()

    db_session.add(_job(idempotency_key="idem-abc"))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_null_idempotency_key_allows_many(db_session):
    """Unkeyed jobs (NULL idempotency_key) are never blocked by the index."""
    _make_parents(db_session)
    db_session.add(_job(idempotency_key=None))
    db_session.add(_job(idempotency_key=None))
    db_session.flush()  # must not raise
    assert db_session.query(GenerationJobORM).count() == 2


def test_same_key_different_tenant_allowed(db_session):
    """Idempotency is scoped per-tenant, not global."""
    _make_parents(db_session)
    db_session.add(TenantORM(tenant_id="t-2", name="T2", slug="t-2"))
    db_session.flush()
    db_session.add(_job(idempotency_key="shared"))
    db_session.add(_job(tenant_id="t-2", idempotency_key="shared"))
    db_session.flush()  # must not raise
    assert db_session.query(GenerationJobORM).count() == 2


def test_expected_indexes_exist(db_inspector):
    """Both the worker-scan index and the idempotency unique index exist."""
    indexes = db_inspector.get_indexes("generation_jobs")
    names = {ix["name"] for ix in indexes}
    assert "ix_generation_jobs_status_created" in names
    assert "uq_generation_jobs_idem" in names
    idem = next(ix for ix in indexes if ix["name"] == "uq_generation_jobs_idem")
    assert idem["unique"]  # SQLite reports 1, Postgres True — truthy either way
    assert idem["column_names"] == ["tenant_id", "idempotency_key"]
