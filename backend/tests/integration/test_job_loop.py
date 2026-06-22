"""Integration tests for the media-generation job loop (Sprint 1).

The behavioral test needs a live Postgres (advisory locks + SKIP LOCKED)
and is skipped until Sprint 2 wires real adapters; the loop code is still
merged and started by the lifespan. The lock-collision check runs without
a DB and guards the 900004 advisory-lock id against the worker.py locks.
"""

import asyncio

import pytest

from app.services.media_gen.job_loop import (
    LOCK_GENERATION_JOBS,
    run_generation_job_loop,
)


def test_advisory_lock_id_does_not_collide_with_worker():
    """900004 must be distinct from worker.py's 900001-900003."""
    from worker import LOCK_DLP_RETENTION, LOCK_LOG_ARCHIVE, LOCK_PARTITION

    worker_locks = {LOCK_DLP_RETENTION, LOCK_LOG_ARCHIVE, LOCK_PARTITION}
    assert LOCK_GENERATION_JOBS == 900004
    assert LOCK_GENERATION_JOBS not in worker_locks


@pytest.mark.skip(reason="requires live Postgres + Sprint 2 adapter wiring")
@pytest.mark.asyncio
async def test_queued_job_marked_no_adapter():
    """Against Postgres: a queued job transitions to failed/no_adapter
    within a couple of poll cycles (no adapter is registered in Sprint 1).

    To enable: seed a tenant + portal_user + a queued generation_jobs row,
    then run the assertions below.
    """
    task = asyncio.create_task(run_generation_job_loop())
    try:
        await asyncio.sleep(8)
    finally:
        task.cancel()
    # row = <SELECT status, error_code FROM generation_jobs WHERE id = ...>
    # assert row.status == "failed"
    # assert row.error_code == "no_adapter"
