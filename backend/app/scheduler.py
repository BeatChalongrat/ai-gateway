"""APScheduler wrapper — single in-process AsyncIO scheduler.

Currently runs one job: the daily ``ModelDiscoveryService.sync_all``
sweep. Started in the FastAPI lifespan, stopped on shutdown.

Why an in-process scheduler (vs. an external cron worker):
- One job, one tick a day, low risk of overlap.
- No new container / sidecar required.
- The admin "sync now" endpoint shares the same code path, so
  scheduled and manual syncs go through identical logic.

The scheduler ticks regardless of which gunicorn worker happens to
run it — every worker will start its own scheduler. For our 24h
cadence + idempotent upserts, multiple workers running the same job
is acceptable redundant work. If/when that becomes expensive we can
gate on a Postgres advisory lock at the start of ``sync_all``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger


if TYPE_CHECKING:
    from app.services.discovery.discovery_service import ModelDiscoveryService


logger = structlog.get_logger(__name__)


scheduler = AsyncIOScheduler()


async def _run_model_discovery(
    discovery_service: "ModelDiscoveryService",
) -> None:
    """Open a fresh DB session for the scheduled sync.

    APScheduler triggers this on its own coroutine — the FastAPI
    request-scoped session machinery is unavailable here, so we mint
    a session via ``SessionLocal`` and close it in ``finally`` to
    match the request-handler pattern.
    """
    from app.core.db import SessionLocal

    db = SessionLocal()
    try:
        result = await discovery_service.sync_all(db)
        logger.info(
            "scheduled_model_discovery_complete",
            **result.to_dict(),
        )
    except Exception as exc:
        logger.warning(
            "scheduled_model_discovery_failed",
            error=str(exc)[:200],
        )
    finally:
        db.close()


def start_scheduler(discovery_service: "ModelDiscoveryService") -> None:
    """Register the daily discovery job and start the scheduler.

    Safe to call multiple times — ``replace_existing=True`` upserts
    the job by id and starting an already-running scheduler is a
    no-op on this APScheduler version.
    """
    scheduler.add_job(
        _run_model_discovery,
        trigger=IntervalTrigger(hours=24),
        kwargs={"discovery_service": discovery_service},
        id="model_discovery_sync",
        replace_existing=True,
        # 1-hour grace if the trigger fires while the loop is paused
        # (e.g. during a deploy) — beats silently skipping the run.
        misfire_grace_time=3600,
        coalesce=True,
    )
    if not scheduler.running:
        scheduler.start()
        logger.info("scheduler_started", job_count=len(scheduler.get_jobs()))


def stop_scheduler() -> None:
    """Shutdown the scheduler without blocking on in-flight jobs."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("scheduler_stopped")
