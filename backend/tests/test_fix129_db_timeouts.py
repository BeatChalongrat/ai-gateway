"""FIX129 A1 — every backend Postgres connection carries its own time limits.

The 2026-09-22 wedge: one session idle in transaction holding a row lock,
statement/lock/idle timeouts all 0 on the server, nothing ever healed. The
backend now sets them per connection (libpq ``options``) and names itself
in pg_stat_activity (``application_name``).
"""

from __future__ import annotations

import pytest

from app.core.db import pg_connect_args, pg_timeouts

_ENV = (
    "DB_STATEMENT_TIMEOUT_MS",
    "DB_LOCK_TIMEOUT_MS",
    "DB_IDLE_IN_TRANSACTION_TIMEOUT_MS",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)


def test_postgres_dsn_gets_timeouts_and_application_name():
    args = pg_connect_args(
        "postgresql+psycopg://u:p@postgres:5432/ai_gateway",
        application_name="sran-backend",
    )
    assert args["application_name"] == "sran-backend"
    assert args["client_encoding"] == "utf8"
    assert "-c statement_timeout=60000" in args["options"]
    assert "-c lock_timeout=10000" in args["options"]
    assert "-c idle_in_transaction_session_timeout=30000" in args["options"]


def test_defaults_are_not_zero():
    t = pg_timeouts()
    assert t["idle_in_transaction_session_timeout"] > 0
    assert t["lock_timeout"] > 0
    assert t["statement_timeout"] > 0


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("DB_IDLE_IN_TRANSACTION_TIMEOUT_MS", "1234")
    monkeypatch.setenv("DB_LOCK_TIMEOUT_MS", "0")
    args = pg_connect_args("postgresql://x/y", application_name="a")
    assert "-c idle_in_transaction_session_timeout=1234" in args["options"]
    assert "-c lock_timeout=0" in args["options"]


def test_sqlite_gets_nothing():
    assert pg_connect_args("sqlite:///x.db", application_name="a") == {}
    assert pg_connect_args("sqlite+aiosqlite:///x.db", application_name="a") == {}
