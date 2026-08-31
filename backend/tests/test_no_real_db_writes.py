"""FIX36 Part 0 — tests must not be able to write to the real database.

Three layers, each pinned here:

1. ``conftest._force_python_log_sink`` — every test runs with
   ``log_worker_engine="python"`` so the app path never even attempts the
   gRPC sink.
2. ``request_log_queue._dispatch_to_grpc`` — FAILS LOUDLY under pytest
   (the gRPC sink writes to the real Postgres through the Go sidecar,
   bypassing SQLAlchemy-level isolation entirely; this is how 214
   test-tenant rows landed in the live ``request_logs``).
3. ``app.core.db.install_test_write_guard`` — any INSERT/UPDATE/DELETE
   that reaches a guarded (real) engine under pytest raises instead of
   writing silently. Reads stay allowed.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine, text

from app.core.db import TestWroteToRealDatabaseError, install_test_write_guard
from app.domain.observability import RequestRecord, RequestStatus
from app.services import request_log_queue


def _record() -> RequestRecord:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    return RequestRecord(
        request_id="req_guard_test",
        tenant_id="test-tenant",
        project_id="test-project",
        provider="openai",
        model="gpt-4o-mini",
        status=RequestStatus.SUCCESS,
        policy_action="allow",
        started_at=now,
        completed_at=now,
        latency_ms=1,
    )


# ---------------------------------------------------------------------------
# Layer 1 — the autouse fixture pins the in-process sink
# ---------------------------------------------------------------------------

def test_every_test_runs_with_the_python_log_sink():
    from app.core.config import settings

    assert settings.log_worker_engine == "python"


# ---------------------------------------------------------------------------
# Layer 2 — the gRPC sink refuses to run under pytest
# ---------------------------------------------------------------------------

def test_grpc_dispatch_fails_loudly_under_pytest(monkeypatch):
    monkeypatch.delenv("SRAN_ALLOW_LOG_SINK_IN_TESTS", raising=False)

    async def _run():
        with pytest.raises(RuntimeError, match="REAL"):
            request_log_queue._singleton._dispatch_to_grpc(_record())

    asyncio.run(_run())


def test_enqueue_with_go_engine_raises_under_pytest_not_silently_writes(monkeypatch):
    from app.core.config import settings

    monkeypatch.delenv("SRAN_ALLOW_LOG_SINK_IN_TESTS", raising=False)
    monkeypatch.setattr(settings, "log_worker_engine", "go")

    async def _run():
        with pytest.raises(RuntimeError, match="log-worker"):
            request_log_queue.enqueue(_record())

    asyncio.run(_run())


def test_grpc_dispatch_allowed_with_explicit_escape_hatch(monkeypatch):
    # With the escape hatch the call proceeds into the normal setup path
    # (and returns False here because the client import/stub setup is not
    # exercised) — the point is that it no longer raises.
    monkeypatch.setenv("SRAN_ALLOW_LOG_SINK_IN_TESTS", "1")

    from app.services.log_worker_client import LogWorkerClient, LogWorkerUnavailable

    def _raise(*_a, **_k):
        raise LogWorkerUnavailable("stubs missing")

    monkeypatch.setattr(LogWorkerClient, "instance", classmethod(_raise))

    async def _run():
        assert request_log_queue._singleton._dispatch_to_grpc(_record()) is False

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Layer 3 — the engine write tripwire
# ---------------------------------------------------------------------------

@pytest.fixture()
def guarded_engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/guarded.db", future=True)
    with eng.begin() as c:  # set up BEFORE the guard so DDL/seed succeeds
        c.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)"))
        c.execute(text("INSERT INTO t (v) VALUES ('seed')"))
    install_test_write_guard(eng)
    return eng


def test_write_guard_blocks_writes_under_pytest(guarded_engine, monkeypatch):
    monkeypatch.delenv("SRAN_ALLOW_REAL_DB_WRITES_IN_TESTS", raising=False)
    for stmt in (
        "INSERT INTO t (v) VALUES ('nope')",
        "UPDATE t SET v = 'nope'",
        "DELETE FROM t",
    ):
        with pytest.raises(TestWroteToRealDatabaseError):
            with guarded_engine.begin() as c:
                c.execute(text(stmt))
    # Nothing landed.
    with guarded_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM t")).scalar() == 1


def test_write_guard_allows_reads(guarded_engine):
    with guarded_engine.connect() as c:
        assert c.execute(text("SELECT v FROM t")).scalar() == "seed"


def test_write_guard_escape_hatch(guarded_engine, monkeypatch):
    monkeypatch.setenv("SRAN_ALLOW_REAL_DB_WRITES_IN_TESTS", "1")
    with guarded_engine.begin() as c:
        c.execute(text("INSERT INTO t (v) VALUES ('deliberate')"))
    with guarded_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM t")).scalar() == 2


def test_real_engines_carry_the_guard():
    from app.core import db

    assert getattr(db.engine, "_sran_test_write_guard", False) is True
