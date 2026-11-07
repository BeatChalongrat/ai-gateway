"""FIX131 — every worker's model registry follows model_configs.

An admin edit (PATCH /admin/models/{id}, "sync now", the capability test,
a new provider key) reloads the in-memory registry of the ONE worker that
handled it. Under gunicorn the other workers kept routing — and billing —
on the old rows until their own daily discovery run or a restart. Each
worker now polls a fingerprint of the active rows and reloads on change.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.services.providers import registry_sync


class _Session:
    def rollback(self):
        pass

    def close(self):
        pass


@pytest.mark.asyncio
async def test_reloads_on_start_and_on_every_change_only():
    prints = iter(["a", "a", "a", "b", "b", "c", "c", "c"] + ["c"] * 50)
    reloads: list[str] = []
    seen: list[str] = []

    def fp(_session):
        v = next(prints)
        seen.append(v)
        return v

    async def reload(_session):
        reloads.append(seen[-1])

    task = asyncio.create_task(
        registry_sync.sync_loop(_Session, reload=reload, interval_s=0.001, fingerprint_fn=fp)
    )
    for _ in range(500):
        await asyncio.sleep(0.002)
        if len(seen) >= 8:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert reloads == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_a_failing_pass_keeps_going():
    calls = {"n": 0}

    def fp(_session):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("db blip")
        return "x"

    reloads = []

    async def reload(_session):
        reloads.append(1)

    task = asyncio.create_task(
        registry_sync.sync_loop(_Session, reload=reload, interval_s=0.001, fingerprint_fn=fp)
    )
    for _ in range(500):
        await asyncio.sleep(0.002)
        if reloads:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls["n"] >= 2 and reloads == [1]


def test_generic_fingerprint_moves_when_the_active_rows_change(tmp_path):
    from app.models.model_config import ModelConfigORM

    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    ModelConfigORM.__table__.create(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    base = dict(provider="openai", model="m1", cost_tier="low", latency_tier="low",
                max_context_tokens=1000, supports_streaming=True, supports_tools=True,
                cost_per_input_token=0.0, cost_per_output_token=0.0)
    cols = {c.name for c in ModelConfigORM.__table__.columns}
    row = ModelConfigORM(**{k: v for k, v in base.items() if k in cols}, is_active=True)
    s.add(row)
    s.commit()
    first = registry_sync.fingerprint(s)
    row.updated_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    s.commit()
    second = registry_sync.fingerprint(s)
    row.is_active = False
    s.commit()
    third = registry_sync.fingerprint(s)
    assert len({first, second, third}) == 3
    s.close()


def test_postgres_fingerprint_covers_every_column_of_every_active_row():
    sql = str(registry_sync._PG_FINGERPRINT)
    assert "m::text" in sql and "WHERE m.is_active" in sql and "ORDER BY m.id" in sql


def test_every_worker_starts_the_follower():
    import app.main as app_main

    src = Path(inspect.getsourcefile(app_main)).read_text(encoding="utf-8")
    tree = ast.parse(src)
    lifespan = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan")
    names = {n.id for n in ast.walk(lifespan) if isinstance(n, ast.Name)}
    assert "_registry_sync_loop" in names


@pytest.mark.asyncio
async def test_discovery_scheduler_restarts_after_a_demotion():
    """A worker can be elected, demoted and elected again: the APScheduler
    instance must start again after stop (leader_jobs on_start/on_stop)."""
    from app import scheduler as sched

    class _Discovery:
        async def sync_all(self, db):  # never reached: 24 h interval
            raise AssertionError

    try:
        sched.start_scheduler(_Discovery())
        assert sched.scheduler.running
        sched.stop_scheduler()
        await asyncio.sleep(0)
        assert not sched.scheduler.running
        sched.start_scheduler(_Discovery())
        assert sched.scheduler.running
        assert sched.scheduler.get_job("model_discovery_sync") is not None
    finally:
        sched.stop_scheduler()
