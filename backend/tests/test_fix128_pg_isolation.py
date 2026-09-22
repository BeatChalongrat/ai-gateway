"""FIX128 B — the unit lane cannot open a real Postgres connection.

Proves the tripwire from ``tests/_pg_isolation.py`` is active in this lane:
the app's own settings URL (the live database, inside the backend container)
and a direct psycopg connect both refuse before a socket is opened, while the
sqlite fixtures keep working.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine, text

from tests import _pg_isolation


def test_tripwire_is_installed_in_the_unit_lane():
    assert not _pg_isolation.allowed(), "the unit lane must never set the opt-in"
    assert _pg_isolation.install() is True


def test_direct_psycopg_connect_is_refused():
    import psycopg

    with pytest.raises(_pg_isolation.RealPostgresInTests):
        psycopg.connect("postgresql://postgres:x@postgres:5432/ai_gateway")
    with pytest.raises(_pg_isolation.RealPostgresInTests):
        psycopg.Connection.connect("postgresql://chatedge:x@postgres-webchat:5432/sran_ai")


def test_async_psycopg_connect_is_refused():
    import psycopg

    async def go():
        await psycopg.AsyncConnection.connect("postgresql://postgres:x@postgres:5432/ai_gateway")

    with pytest.raises(_pg_isolation.RealPostgresInTests):
        asyncio.run(go())


def test_an_engine_built_from_the_app_settings_cannot_write_to_the_live_db():
    from app.core.config import settings

    url = str(settings.database_url or "")
    if not url.startswith("postgres"):
        pytest.skip("settings do not name a Postgres URL in this environment")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(url, pool_pre_ping=False)
    with pytest.raises(Exception) as exc:
        with engine.connect() as conn:
            conn.execute(text("select 1"))
    assert "real Postgres" in str(exc.value) or isinstance(exc.value.__cause__, _pg_isolation.RealPostgresInTests) or "RealPostgresInTests" in repr(exc.value)


def test_sqlite_still_works():
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        assert conn.execute(text("select 1")).scalar() == 1
