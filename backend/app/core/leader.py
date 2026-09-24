"""FIX131 — one leader among the backend's worker processes.

The backend runs as N gunicorn workers (GUNICORN_WORKERS), and during a deploy
two containers overlap (scripts/deploy-backend.sh). Every worker runs the
FastAPI lifespan, so a background job started there runs N times — fine for
what describes the worker itself (loop monitor, pool sampler, its request-log
queue), wrong for what acts on the world: the alert sweep sends a phone
notification per worker, discovery and the search-health probe call providers
per worker, the retention pipeline moves rows per worker. Those jobs run only
in the worker that holds the LEADER LOCK (``main.py`` lists them).

The lock is a session-level Postgres advisory lock, id ``LEADER_LOCK_ID``
(900009 — the 9000xx block main.py documents), held on a dedicated
AUTOCOMMIT connection that belongs to no pool:

* The server drops it the moment the holding session ends — a clean exit, a
  crash, a SIGKILL all close the socket — so a follower takes over on its
  next attempt (``retry_s``) with no lease TTL to tune. (A Redis lease was the
  alternative; it needs a TTL, and an event-loop stall longer than the TTL
  gives two leaders while the first still believes it leads. Postgres is also
  already the one dependency every leader job needs.)
* It spans processes AND containers, so the old and the new container of a
  deploy still elect one leader between them — a gunicorn-master mechanism
  would not.
* AUTOCOMMIT: the connection is never "idle in transaction", so neither
  idle_in_transaction_session_timeout (FIX129) kills it nor does the
  pg_activity sampler count it as stuck.

The leader re-checks every ``heartbeat_s`` that its own session still holds
the lock (``pg_locks``). If the connection is gone the server has already
released the lock, so another worker may take over before this one notices;
the overlap is at most ``heartbeat_s`` and the leader jobs tolerate it (alert
cooldowns live in the DB, maintenance is idempotent). A job already running
in a thread finishes its run; only its loop is cancelled.

Needs a DIRECT Postgres session: behind PgBouncer transaction pooling a
session lock is meaningless (docs/SCALING_ACTIVATION_RUNBOOK.md). On a
non-Postgres URL (sqlite tests, a bare dev run) the lock always succeeds —
one process, one leader — like the other advisory-lock helpers here.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from typing import Protocol

import structlog

logger = structlog.get_logger(__name__)

# 9000xx block (grep "pg_advisory" before adding one): worker.py 900001-900003,
# media job loop 900004, startup seeds 900005/900008 (900006 retired), schema
# guard 900007, default alert rules seed 900010 (main.py).
LEADER_LOCK_ID = 900009


class AdvisoryLock(Protocol):
    """What the election needs from a lock. Sync: it is called in a thread."""

    def try_acquire(self) -> bool: ...

    def still_held(self) -> bool: ...

    def release(self) -> None: ...


class LocalLock:
    """Always acquirable — a single process is its own leader (non-Postgres)."""

    def try_acquire(self) -> bool:
        return True

    def still_held(self) -> bool:
        return True

    def release(self) -> None:
        return None


_HELD_SQL = (
    "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' "
    "AND pid = pg_backend_pid() AND granted AND objsubid = 1 "
    "AND (classid::bigint * 4294967296 + objid::bigint) = :id)"
)


class PgAdvisoryLock:
    """A session-level ``pg_try_advisory_lock`` on one dedicated connection."""

    def __init__(self, url: str, lock_id: int = LEADER_LOCK_ID, *, connect_args: dict | None = None,
                 engine=None) -> None:
        from sqlalchemy import create_engine
        from sqlalchemy.pool import NullPool

        self.lock_id = int(lock_id)
        self._engine = engine or create_engine(
            url,
            poolclass=NullPool,
            isolation_level="AUTOCOMMIT",
            connect_args=connect_args or {},
        )
        self._conn = None

    def _connection(self):
        if self._conn is None:
            self._conn = self._engine.connect()
        return self._conn

    def try_acquire(self) -> bool:
        from sqlalchemy import text

        try:
            got = self._connection().execute(
                text("SELECT pg_try_advisory_lock(:id)"), {"id": self.lock_id}
            ).scalar()
        except Exception:
            self._drop()
            raise
        # A follower keeps its one idle connection for the next attempt
        # (one per worker) rather than reconnecting every retry_s.
        return bool(got)

    def still_held(self) -> bool:
        from sqlalchemy import text

        if self._conn is None:
            return False
        try:
            return bool(self._conn.execute(text(_HELD_SQL), {"id": self.lock_id}).scalar())
        except Exception:
            self._drop()
            return False

    def release(self) -> None:
        from sqlalchemy import text

        if self._conn is None:
            return
        try:
            self._conn.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": self.lock_id})
        except Exception:  # noqa: BLE001 — closing the session releases it anyway
            pass
        self._drop()

    def _drop(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def make_leader_lock(url: str, *, application_name: str = "sran-backend-leader") -> AdvisoryLock:
    """The production lock for ``url``: Postgres advisory lock, else local."""
    if not url.startswith(("postgres://", "postgresql://", "postgresql+")):
        return LocalLock()
    from app.core.db import pg_connect_args

    args = pg_connect_args(url, application_name=application_name)
    # Fail fast instead of parking a thread on a dead host; keepalives so a
    # silently vanished server is noticed by the heartbeat, not in 2 hours.
    args.update({"connect_timeout": 5, "keepalives": 1, "keepalives_idle": 30,
                 "keepalives_interval": 10, "keepalives_count": 3})
    return PgAdvisoryLock(url, LEADER_LOCK_ID, connect_args=args)


Callback = Callable[[], Awaitable[None]]


class LeaderElection:
    """Try for the lock; while holding it run ``on_elected``/``on_demoted``.

    ``run()`` loops until cancelled. Lock calls are sync and run in a thread,
    each bounded by ``op_timeout_s``; a call that errors or times out counts
    as "not leader" (a leader demotes itself).
    """

    def __init__(self, lock: AdvisoryLock, *, on_elected: Callback, on_demoted: Callback,
                 retry_s: float = 5.0, heartbeat_s: float = 5.0, op_timeout_s: float = 15.0,
                 name: str = "backend") -> None:
        self._lock = lock
        self._on_elected = on_elected
        self._on_demoted = on_demoted
        self.retry_s = retry_s
        self.heartbeat_s = heartbeat_s
        self.op_timeout_s = op_timeout_s
        self.name = name
        self.is_leader = False
        self.since: float | None = None

    async def _call(self, fn) -> bool:
        try:
            return bool(await asyncio.wait_for(asyncio.to_thread(fn), timeout=self.op_timeout_s))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a failed call is "not leader"
            logger.warning("leader_lock_call_failed", election=self.name, error=f"{type(exc).__name__}: {str(exc)[:160]}")
            return False

    async def step(self) -> None:
        """One iteration: acquire when following, verify when leading."""
        if not self.is_leader:
            if await self._call(self._lock.try_acquire):
                self.is_leader = True
                self.since = time.time()
                logger.info("leader_elected", election=self.name, pid=os.getpid())
                try:
                    await self._on_elected()
                except Exception as exc:  # noqa: BLE001 — keep leading; jobs log their own failures
                    logger.warning("leader_on_elected_failed", election=self.name, error=str(exc)[:200])
            return
        if not await self._call(self._lock.still_held):
            logger.warning("leader_lost", election=self.name, pid=os.getpid())
            await self._demote()

    async def _demote(self) -> None:
        if not self.is_leader:
            return
        self.is_leader = False
        self.since = None
        try:
            await self._on_demoted()
        except Exception as exc:  # noqa: BLE001
            logger.warning("leader_on_demoted_failed", election=self.name, error=str(exc)[:200])

    async def run(self) -> None:
        while True:
            await self.step()
            await asyncio.sleep(self.heartbeat_s if self.is_leader else self.retry_s)

    async def resign(self) -> None:
        """Shutdown: stop the leader jobs, then give the lock back at once."""
        await self._demote()
        try:
            await asyncio.wait_for(asyncio.to_thread(self._lock.release), timeout=self.op_timeout_s)
        except Exception:  # noqa: BLE001 — process exit closes the session anyway
            pass


class LeaderJobs:
    """The once-per-deployment jobs: started on election, cancelled on demotion."""

    def __init__(self, jobs: dict[str, Callable[[], Awaitable[None]]], *,
                 on_start: Callable[[], None] | None = None,
                 on_stop: Callable[[], None] | None = None) -> None:
        self._jobs = jobs
        self._on_start = on_start
        self._on_stop = on_stop
        self.tasks: dict[str, asyncio.Task] = {}

    @property
    def names(self) -> list[str]:
        return list(self._jobs)

    async def start(self) -> None:
        for name, factory in self._jobs.items():
            if name not in self.tasks or self.tasks[name].done():
                self.tasks[name] = asyncio.create_task(factory(), name=f"leader:{name}")
        if self._on_start is not None:
            self._on_start()

    async def stop(self) -> None:
        if self._on_stop is not None:
            try:
                self._on_stop()
            except Exception:  # noqa: BLE001
                pass
        tasks, self.tasks = list(self.tasks.values()), {}
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


# The election this process runs (set by main.py's lifespan), so /ready and the
# worker board can say whether THIS worker leads without a DB call.
_current: LeaderElection | None = None


def set_current(election: LeaderElection | None) -> None:
    global _current
    _current = election


def is_leader() -> bool:
    return bool(_current is not None and _current.is_leader)
