"""SSO Phase 1 (A1) — prove the tenant_id NOT NULL contract on real Postgres.

The unit suite runs on SQLite via ``metadata.create_all``; after A1 the
ORM declares ``portal_users.tenant_id`` NOT NULL so even SQLite enforces
it, but this module additionally proves the behaviour on a real Postgres
(the engine migration 030 actually targets):

* an OAuth-created user INSERTs cleanly with the central tenant attached;
* a tenant-less INSERT is refused by the database, not just by app code.

Lane wiring: needs a disposable Postgres. Set ``TEST_POSTGRES_URL`` (e.g.
``postgresql+psycopg://gw:gw@localhost:5432/gw_test``) to run; otherwise
the module SKIPS LOUDLY — it must never silently pass without a database.
Tagged ``integration`` so the default unit lane (-m "not integration")
excludes it either way.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.integration

_PG_URL = os.environ.get("TEST_POSTGRES_URL", "").strip()

if not _PG_URL:
    pytest.skip(
        "TEST_POSTGRES_URL not set — Postgres NOT NULL lane skipped "
        "(set it to a disposable postgres+psycopg URL to run)",
        allow_module_level=True,
    )


@pytest.fixture
def pg_session():
    from app.core.db import Base
    import app.models  # noqa: F401

    engine = create_engine(_PG_URL)
    # Isolated schema per run so repeated runs / parallel lanes don't
    # collide with each other or with a real dev database.
    schema = f"notnull_test_{uuid.uuid4().hex[:12]}"
    with engine.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        conn.exec_driver_sql(f'SET search_path TO "{schema}"')
        Base.metadata.create_all(conn)
    Session = sessionmaker(bind=engine)
    s = Session()
    s.execute(__import__("sqlalchemy").text(f'SET search_path TO "{schema}"'))
    try:
        yield s
    finally:
        s.rollback()
        s.close()
        with engine.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        engine.dispose()


class TestTenantNotNullOnPostgres:
    def test_oauth_user_insert_satisfies_not_null(self, pg_session, monkeypatch):
        from app.core.config import settings
        from app.models.portal_user import PortalUserORM
        from app.models.tenant import TenantORM
        from app.services.portal import oauth_service

        monkeypatch.setattr(settings, "allowed_email_domains", "gbtech.co.th")
        monkeypatch.setattr(settings, "central_tenant_slug", "gbtech")
        monkeypatch.setattr(settings, "central_tenant_name", "GB Tech")

        result = oauth_service.login_or_create(
            pg_session,
            provider="google",
            oauth_id="pg-sub-1",
            email="pg@gbtech.co.th",
            display_name="PG User",
            avatar_url=None,
            email_verified=True,
        )
        user = pg_session.get(PortalUserORM, result["user"]["id"])
        assert user.tenant_id is not None
        tenant = pg_session.execute(
            select(TenantORM).where(TenantORM.tenant_id == user.tenant_id)
        ).scalar_one()
        assert tenant.slug == "gbtech"

    def test_tenantless_insert_is_refused_by_db(self, pg_session):
        from sqlalchemy.exc import IntegrityError

        from app.models.portal_user import PortalUserORM

        pg_session.add(
            PortalUserORM(
                id=str(uuid.uuid4()),
                email="orphan@gbtech.co.th",
                password_hash="x",
                status="active",
            )
        )
        with pytest.raises(IntegrityError):
            pg_session.flush()
