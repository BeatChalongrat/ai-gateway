"""FIX131 — the once-per-deployment jobs run in ONE worker (leader election).

Under gunicorn every worker runs main.py's lifespan. Before FIX131 the alert
sweep, the nightly retention pipeline, partition maintenance, the search
health probes and the model-discovery scheduler were started there directly,
so N workers ran them N times (N phone alerts per incident, N paid probes).

These tests prove, without a database:
* two elections over one lock -> exactly one leader, its jobs run once;
* the leader exits (resign) or loses its session -> the other takes over;
* the production lock issues the session-level Postgres calls on ONE
  autocommit connection, and verifies ownership through pg_locks;
* main.py starts those jobs only through the election (structural guard);
* the default alert-rule seed is serialized across workers (lock 900010).
"""

from __future__ import annotations

import ast
import asyncio
import inspect
from pathlib import Path

import pytest

from app.core import leader as leader_mod
from app.core.leader import (
    LEADER_LOCK_ID,
    LeaderElection,
    LeaderJobs,
    LocalLock,
    PgAdvisoryLock,
    make_leader_lock,
)


# ---------------------------------------------------------------------------
# A fake "server": one lock, many sessions (like pg advisory locks).
# ---------------------------------------------------------------------------


class _Server:
    def __init__(self) -> None:
        self.holder: object | None = None


class _FakeSessionLock:
    def __init__(self, server: _Server) -> None:
        self.server = server
        self.connected = True
        self.released = False

    def try_acquire(self) -> bool:
        if not self.connected:
            raise ConnectionError("server closed the connection")
        if self.server.holder in (None, self):
            self.server.holder = self
            return True
        return False

    def still_held(self) -> bool:
        return self.connected and self.server.holder is self

    def release(self) -> None:
        self.released = True
        if self.server.holder is self:
            self.server.holder = None

    def drop_connection(self) -> None:
        """What a dead session looks like: the server frees the lock at once."""
        self.connected = False
        if self.server.holder is self:
            self.server.holder = None


def _election(lock, log: list, name: str) -> LeaderElection:
    async def elected():
        log.append(("elected", name))

    async def demoted():
        log.append(("demoted", name))

    return LeaderElection(lock, on_elected=elected, on_demoted=demoted, retry_s=0.01,
                          heartbeat_s=0.01, op_timeout_s=2.0, name=name)


@pytest.mark.asyncio
async def test_one_leader_among_workers_then_takeover_on_exit():
    server, log = _Server(), []
    a = _election(_FakeSessionLock(server), log, "a")
    b = _election(_FakeSessionLock(server), log, "b")
    c = _election(_FakeSessionLock(server), log, "c")
    for _ in range(3):
        for e in (a, b, c):
            await e.step()
    assert [e.is_leader for e in (a, b, c)] == [True, False, False]
    assert log == [("elected", "a")]

    # The leader worker exits (lifespan shutdown -> resign releases the lock).
    await a.resign()
    assert not a.is_leader and log[-1] == ("demoted", "a")
    await b.step()
    await c.step()
    assert b.is_leader and not c.is_leader
    assert [x for x in log if x[0] == "elected"] == [("elected", "a"), ("elected", "b")]


@pytest.mark.asyncio
async def test_leader_that_loses_its_session_steps_down_and_another_takes_over():
    server, log = _Server(), []
    lock_a = _FakeSessionLock(server)
    a = _election(lock_a, log, "a")
    b = _election(_FakeSessionLock(server), log, "b")
    await a.step()
    await b.step()
    assert a.is_leader and not b.is_leader

    lock_a.drop_connection()          # Postgres restarted / connection killed
    await b.step()                     # the lock is free on the server side
    assert b.is_leader
    await a.step()                     # a's heartbeat notices and demotes
    assert not a.is_leader
    assert ("demoted", "a") in log


@pytest.mark.asyncio
async def test_a_failing_lock_call_is_not_leadership():
    class Boom:
        def try_acquire(self):
            raise OSError("db down")

        def still_held(self):
            return False

        def release(self):
            pass

    log: list = []
    e = _election(Boom(), log, "x")
    await e.step()
    assert not e.is_leader and log == []


@pytest.mark.asyncio
async def test_run_loop_elects_and_is_cancellable():
    server, log = _Server(), []
    e = _election(_FakeSessionLock(server), log, "a")
    task = asyncio.create_task(e.run())
    for _ in range(50):
        await asyncio.sleep(0.01)
        if e.is_leader:
            break
    assert e.is_leader
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_leader_jobs_start_once_and_stop_cancels_them():
    started, stopped = [], []
    runs = {"n": 0}

    async def job():
        runs["n"] += 1
        await asyncio.sleep(3600)

    jobs = LeaderJobs({"sweep": job}, on_start=lambda: started.append(1), on_stop=lambda: stopped.append(1))
    await jobs.start()
    await jobs.start()                 # a second election call must not double it
    await asyncio.sleep(0)
    assert runs["n"] == 1 and len(jobs.tasks) == 1
    task = jobs.tasks["sweep"]
    await jobs.stop()
    assert task.cancelled() and jobs.tasks == {}
    assert started and stopped


def test_is_leader_reflects_the_current_election():
    server = _Server()
    e = _election(_FakeSessionLock(server), [], "a")
    leader_mod.set_current(e)
    try:
        assert leader_mod.is_leader() is False
        e.is_leader = True
        assert leader_mod.is_leader() is True
    finally:
        leader_mod.set_current(None)
    assert leader_mod.is_leader() is False


# ---------------------------------------------------------------------------
# The production lock: the SQL it sends, on one autocommit connection.
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _Conn:
    def __init__(self, log, answers):
        self.log, self.answers, self.closed = log, answers, False

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.log.append((sql, dict(params or {})))
        for key, value in self.answers.items():
            if key in sql:
                return _Result(value)
        return _Result(None)

    def close(self):
        self.closed = True


class _Engine:
    def __init__(self, answers):
        self.log, self.answers, self.conns = [], answers, []

    def connect(self):
        c = _Conn(self.log, self.answers)
        self.conns.append(c)
        return c


def test_pg_lock_uses_one_session_and_session_level_calls():
    eng = _Engine({"pg_try_advisory_lock": True, "pg_locks": True})
    lock = PgAdvisoryLock("postgresql+psycopg://x/y", engine=eng)
    assert lock.try_acquire() is True
    assert lock.still_held() is True
    lock.release()
    sqls = [s for s, _ in eng.log]
    assert "pg_try_advisory_lock(:id)" in sqls[0]
    assert "pg_advisory_xact_lock" not in " ".join(sqls)     # session-level, not xact
    assert "pg_backend_pid()" in sqls[1] and "pg_locks" in sqls[1]
    assert "pg_advisory_unlock(:id)" in sqls[2]
    assert all(p == {"id": LEADER_LOCK_ID} for _, p in eng.log)
    assert len(eng.conns) == 1 and eng.conns[0].closed      # released = session closed


def test_pg_lock_follower_keeps_its_connection_and_a_dead_one_is_not_held():
    eng = _Engine({"pg_try_advisory_lock": False})
    lock = PgAdvisoryLock("postgresql+psycopg://x/y", engine=eng)
    assert lock.try_acquire() is False
    assert lock.try_acquire() is False
    assert len(eng.conns) == 1                                # no reconnect per retry

    class Dead(_Conn):
        def execute(self, stmt, params=None):
            raise ConnectionError("gone")

    lock._conn = Dead([], {})
    assert lock.still_held() is False
    assert lock._conn is None


def test_leader_lock_is_local_off_postgres_and_pg_on_postgres():
    assert isinstance(make_leader_lock("sqlite:///x.db"), LocalLock)
    pg = make_leader_lock("postgresql+psycopg://u:p@127.0.0.1:1/db")
    assert isinstance(pg, PgAdvisoryLock)
    assert pg._engine.dialect.name == "postgresql"


def test_lock_ids_do_not_collide_with_the_9000xx_block():
    import app.main as app_main
    from app.core.schema_guard import SCHEMA_MIGRATE_LOCK_ID
    from app.services.media_gen.job_loop import LOCK_GENERATION_JOBS

    ids = [900001, 900002, 900003, LOCK_GENERATION_JOBS, app_main.STARTUP_LOCK_SEED_ADMIN,
           900006, SCHEMA_MIGRATE_LOCK_ID, app_main.STARTUP_LOCK_PROVIDER_ENV_SEED,
           LEADER_LOCK_ID, app_main.STARTUP_LOCK_ALERT_RULES_SEED]
    assert len(set(ids)) == len(ids)
    assert LEADER_LOCK_ID == 900009 and app_main.STARTUP_LOCK_ALERT_RULES_SEED == 900010


# ---------------------------------------------------------------------------
# main.py: the jobs that act on the world start only through the election.
# ---------------------------------------------------------------------------

_LEADER_ONLY = {
    "_alert_evaluation_sweep",
    "_nightly_retention_pipeline",
    "_daily_partition_maintenance",
    "search_health_monitor_loop",
    "_start_model_discovery_scheduler",
}


def _lifespan_node() -> ast.AsyncFunctionDef:
    import app.main as app_main

    tree = ast.parse(Path(inspect.getsourcefile(app_main)).read_text(encoding="utf-8"))
    return next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan")


def test_lifespan_never_calls_a_leader_only_job_directly():
    direct = []
    for node in ast.walk(_lifespan_node()):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _LEADER_ONLY:
            direct.append(node.func.id)
    assert direct == [], f"started in EVERY worker, not only the leader: {direct}"


def test_lifespan_hands_every_leader_only_job_to_the_election():
    handed: set[str] = set()
    for node in ast.walk(_lifespan_node()):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "LeaderJobs":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name) and sub.id in _LEADER_ONLY:
                    handed.add(sub.id)
    assert handed == _LEADER_ONLY


def test_default_alert_rules_seed_takes_its_startup_lock_first(monkeypatch):
    import app.core.db as core_db
    import app.main as app_main
    import app.services.alert_defaults as alert_defaults

    calls = []

    class _Session:
        def commit(self):
            calls.append("commit")

        def rollback(self):
            calls.append("rollback")

        def close(self):
            calls.append("close")

    monkeypatch.setattr(core_db, "SessionLocal", lambda: _Session())
    monkeypatch.setattr(app_main, "_acquire_startup_lock",
                        lambda session, lock_id: calls.append(("lock", lock_id)))
    monkeypatch.setattr(alert_defaults, "ensure_default_rules", lambda session: calls.append("seed") or [])
    app_main._seed_default_alert_rules()
    assert calls[:2] == [("lock", 900010), "seed"]
