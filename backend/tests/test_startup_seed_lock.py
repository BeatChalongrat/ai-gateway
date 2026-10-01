"""Phase 12 A1 — multi-worker startup seeding race.

``app.main._seed_admin_user`` is check-then-insert; 4 workers booting on a
fresh DB used to race the existence check and double-insert (or crash into
the unique constraint and abort with an ``[startup] ... skipped`` line).
Phase 12 A1 wraps it in ``pg_advisory_xact_lock`` (id 900005, distinct from
worker.py's 900001-900003 and job_loop's 900004).

(The sibling ``_import_env_provider_keys`` step and its lock id 900006 were
removed when provider keys became admin-UI-only — there is no env key to
import any more.)

How these tests are made meaningful on the SQLite test DB (which has no
advisory locks):

* ``TestPostgresPathTakesLock`` asserts against a recording fake session
  whose dialect reports ``postgresql`` that the helper actually issues
  ``SELECT pg_advisory_xact_lock(:id)`` with the right id — i.e. the
  production path takes the lock.
* ``TestConcurrentSeeding`` monkeypatches ``_acquire_startup_lock`` with
  a fake that reproduces xact-lock SEMANTICS (block until the holder's
  session ends, i.e. its transaction commits/rolls back — release is
  hooked into ``session.close()``, which the production ``finally:``
  always reaches after commit/rollback) and runs 4 concurrent
  invocations against a file-based SQLite DB. The race window is
  widened deterministically by making ``bcrypt.hashpw`` (which sits
  between the SELECT and the INSERT) sleep. Assertions: exactly one row
  AND no ``skipped`` error lines. Temporarily reverting the lock call
  in ``main.py`` (or neutering the fake) makes ≥1 worker hit the
  IntegrityError/lock-contention path and fail the assertion — the
  documented fails-before proof.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import defaultdict

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.main as app_main
from app.core.db import Base


# ---------------------------------------------------------------------------
# The postgres path takes the advisory lock
# ---------------------------------------------------------------------------


class _RecordingSession:
    """Duck-typed session whose bind claims to be postgres."""

    class _Dialect:
        name = "postgresql"

    class _Bind:
        dialect = None  # set below

    def __init__(self) -> None:
        self.bind = _RecordingSession._Bind()
        self.bind.dialect = _RecordingSession._Dialect()
        self.executed: list[tuple[str, dict]] = []

    def execute(self, clause, params=None):
        self.executed.append((str(clause), params or {}))


class TestPostgresPathTakesLock:
    def test_helper_issues_xact_lock_with_id(self):
        sess = _RecordingSession()
        app_main._acquire_startup_lock(sess, app_main.STARTUP_LOCK_SEED_ADMIN)
        assert len(sess.executed) == 1
        sql, params = sess.executed[0]
        assert "pg_advisory_xact_lock" in sql
        assert params == {"id": 900005}

    def test_helper_noops_on_sqlite(self):
        sess = _RecordingSession()
        sess.bind.dialect = type("D", (), {"name": "sqlite"})()
        app_main._acquire_startup_lock(sess, 900005)
        assert sess.executed == []

    def test_lock_ids_are_distinct_and_unclaimed(self):
        """900005 must not collide with worker.py (900001-900003), the
        generation job loop (900004), or the schema guard (900007)."""
        from app.core.schema_guard import SCHEMA_MIGRATE_LOCK_ID
        from app.services.media_gen.job_loop import LOCK_GENERATION_JOBS

        claimed = {
            900001, 900002, 900003, LOCK_GENERATION_JOBS, SCHEMA_MIGRATE_LOCK_ID
        }
        assert app_main.STARTUP_LOCK_SEED_ADMIN not in claimed

    def test_env_key_import_lock_is_gone(self):
        """The env-key importer was removed with the env key source; its
        lock id must not come back silently attached to something else."""
        assert not hasattr(app_main, "STARTUP_LOCK_ENV_KEY_IMPORT")
        assert not hasattr(app_main, "_import_env_provider_keys")

    def test_seed_calls_the_lock_helper(self, monkeypatch, tmp_path):
        """Each startup step must acquire ITS OWN lock id before touching
        the table."""
        Session = _file_sessionmaker(tmp_path, "lockcall.db")
        monkeypatch.setattr("app.core.db.SessionLocal", Session)
        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.setenv("INITIAL_ADMIN_EMAIL", "admin@corp.test")
        monkeypatch.delenv("INITIAL_ADMIN_PASSWORD", raising=False)

        acquired: list[int] = []

        def _record(session, lock_id):
            acquired.append(lock_id)

        monkeypatch.setattr(app_main, "_acquire_startup_lock", _record)

        app_main._seed_admin_user()
        assert acquired == [app_main.STARTUP_LOCK_SEED_ADMIN]


# ---------------------------------------------------------------------------
# Concurrency: 4 "workers" against a fresh DB
# ---------------------------------------------------------------------------


def _file_sessionmaker(tmp_path, name: str):
    """File-based SQLite so each thread gets its OWN connection (a
    StaticPool :memory: DB would share one connection and serialize the
    race away). Short busy-timeout so the fails-before run doesn't hang."""
    engine = create_engine(
        f"sqlite:///{tmp_path / name}",
        connect_args={"check_same_thread": False, "timeout": 2},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


class _FakeXactLock:
    """In-process stand-in for ``pg_advisory_xact_lock`` semantics.

    Acquire blocks until the current holder's transaction ends. The
    production code paths always end their transaction via the
    ``finally: session.close()`` (commit happens before it, rollback on
    error, and ``close()`` itself rolls back any still-open
    transaction), so release is hooked into ``session.close``.
    """

    def __init__(self) -> None:
        self.locks: dict[int, threading.Lock] = defaultdict(threading.Lock)
        self.acquired_ids: list[int] = []

    def __call__(self, session, lock_id: int) -> None:
        lock = self.locks[lock_id]
        lock.acquire()
        self.acquired_ids.append(lock_id)
        original_close = session.close

        def _close_and_release():
            try:
                original_close()
            finally:
                lock.release()

        session.close = _close_and_release


async def _run_concurrently(fn, n: int = 4):
    await asyncio.gather(*[asyncio.to_thread(fn) for _ in range(n)])


class TestConcurrentSeeding:
    @pytest.mark.asyncio
    async def test_concurrent_seed_creates_exactly_one_admin(
        self, monkeypatch, tmp_path, capsys
    ):
        from app.models.admin_user import AdminUserORM

        Session = _file_sessionmaker(tmp_path, "seed.db")
        monkeypatch.setattr("app.core.db.SessionLocal", Session)
        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.delenv("INITIAL_ADMIN_PASSWORD", raising=False)
        monkeypatch.delenv("INITIAL_ADMIN_USERNAME", raising=False)
        # The address is configuration (app.core.first_admin): without it nothing is seeded.
        monkeypatch.setenv("INITIAL_ADMIN_EMAIL", "admin@corp.test")

        # Widen the race window deterministically: bcrypt.hashpw runs
        # BETWEEN the existence SELECT and the INSERT, so a sleep here
        # guarantees every unserialized worker passes the check before
        # any of them inserts.
        import bcrypt

        monkeypatch.setattr(
            bcrypt,
            "hashpw",
            lambda pw, salt: time.sleep(0.15) or b"$2b$04$" + b"x" * 53,
        )

        fake_lock = _FakeXactLock()
        monkeypatch.setattr(app_main, "_acquire_startup_lock", fake_lock)

        await _run_concurrently(app_main._seed_admin_user, n=4)

        with Session() as s:
            admins = s.execute(select(AdminUserORM)).scalars().all()
        assert len(admins) == 1, f"expected exactly 1 admin, got {len(admins)}"
        # No worker may hit the error path (pre-lock, the losers crashed
        # into the unique constraint / lock contention and printed this).
        out = capsys.readouterr().out
        assert "Admin seed skipped" not in out
        # All 4 workers went through the seed-admin lock id.
        assert fake_lock.acquired_ids == [app_main.STARTUP_LOCK_SEED_ADMIN] * 4
