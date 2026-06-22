"""Lifespan job loop for async media generation (Sprint 1).

Polls ``generation_jobs`` for queued work and advances it under Postgres
advisory lock ``900004`` (worker.py owns 900001-900003). The advisory
lock serialises the CLAIM across FastAPI workers; ``FOR UPDATE SKIP
LOCKED`` is belt-and-braces so two workers never grab the same row.

Sprint 1 has NO adapter registered: ``_resolve_adapter`` raises
``NotImplementedError`` and the loop marks the claimed job
``failed`` / ``error_code='no_adapter'``. That is the CORRECT, expected
behavior for this sprint — Sprint 2 plugs in the real Replicate adapter
and the same loop will drive jobs to completion.

The loop is Postgres-only (advisory locks + SKIP LOCKED). On any other
dialect it idles harmlessly so a misconfigured/dev environment can't
crash-loop. It is cancellation-safe: the lifespan cancels the task on
shutdown, mirroring the existing daily loops in ``main.py``.
"""

from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy import text

log = logging.getLogger(__name__)

# Advisory-lock id for the generation job claim. worker.py uses
# 900001/900002/900003 (see backend/worker.py:42-44); 900004 is ours.
LOCK_GENERATION_JOBS = 900004

# Bound on jobs claimed per wake-up so one busy cycle can't starve the
# sleep / cancellation check indefinitely.
_MAX_PER_CYCLE = 50


def _poll_interval() -> float:
    try:
        return max(1.0, float(os.getenv("GENERATION_JOB_POLL_INTERVAL", "5")))
    except (TypeError, ValueError):
        return 5.0


def _resolve_adapter(provider: str):
    """Sprint 1 stub — no adapters are registered yet.

    Sprint 2 replaces this with a real registry lookup returning a
    ``MediaGenAdapter``. Until then every claimed job fails fast with a
    stable ``no_adapter`` code.
    """
    raise NotImplementedError(f"no media-gen adapter for provider {provider!r}")


def _is_postgres(session) -> bool:
    try:
        return session.bind.dialect.name == "postgresql"
    except Exception:  # noqa: BLE001 — best-effort dialect probe
        return False


async def _claim_and_process_one(session) -> bool:
    """Claim one queued job under the advisory lock and advance it.

    Returns True if a job was claimed this cycle, False if none were
    available (or the advisory lock is held elsewhere).
    """
    got = (
        await session.execute(
            text("SELECT pg_try_advisory_lock(:id)"),
            {"id": LOCK_GENERATION_JOBS},
        )
    ).scalar()
    if not got:
        return False

    job_id: str | None = None
    provider: str | None = None
    try:
        row = (
            await session.execute(
                text(
                    "SELECT id, provider, job_type FROM generation_jobs "
                    "WHERE status = 'queued' ORDER BY created_at "
                    "LIMIT 1 FOR UPDATE SKIP LOCKED"
                )
            )
        ).first()
        if row is None:
            return False
        job_id, provider = row[0], row[1]
        await session.execute(
            text(
                "UPDATE generation_jobs "
                "SET status = 'processing', started_at = now() "
                "WHERE id = :id"
            ),
            {"id": job_id},
        )
        await session.commit()
    finally:
        await session.execute(
            text("SELECT pg_advisory_unlock(:id)"),
            {"id": LOCK_GENERATION_JOBS},
        )
        await session.commit()

    if job_id is None:
        return False

    # Sprint 1: no adapter → fail fast with a stable code (Sprint 2 swaps
    # _resolve_adapter for a real registry and processes the job here).
    try:
        _resolve_adapter(provider or "")
    except NotImplementedError:
        await session.execute(
            text(
                "UPDATE generation_jobs SET status = 'failed', "
                "error_code = 'no_adapter', "
                "error_message = 'no media-gen adapter registered (Sprint 1)', "
                "completed_at = now() WHERE id = :id"
            ),
            {"id": job_id},
        )
        await session.commit()
        log.info("generation job %s marked failed: no_adapter", job_id)
    return True


async def run_generation_job_loop() -> None:
    """Background lifespan loop. Cancelled on app shutdown."""
    interval = _poll_interval()
    log.info(
        "generation job loop starting (interval=%ss, advisory_lock=%s)",
        interval,
        LOCK_GENERATION_JOBS,
    )
    from app.core.db import get_async_session_factory

    while True:
        try:
            factory = get_async_session_factory()
            async with factory() as session:
                if _is_postgres(session):
                    for _ in range(_MAX_PER_CYCLE):
                        if not await _claim_and_process_one(session):
                            break
                else:
                    log.debug(
                        "generation job loop idle: non-postgres dialect"
                    )
        except asyncio.CancelledError:
            log.info("generation job loop cancelled")
            raise
        except Exception:  # noqa: BLE001 — a failed cycle must not kill the loop
            log.exception("generation job loop cycle failed")

        await asyncio.sleep(interval)
