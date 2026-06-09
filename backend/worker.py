"""Background worker — runs the singleton scheduled jobs in ONE process,
independent of the web pods, so they don't execute N times across N backend
replicas.

INERT by default: this module is never imported by the web app. It runs only
as the `worker` container under the compose "scaling" profile. See
SCALING_ACTIVATION_RUNBOOK.md for activation (and removing the equivalent jobs
from the web lifespan so they don't double-run).

Why a SYNC worker on a DIRECT Postgres connection:
  * The real jobs are SYNC and take a caller-supplied sync ``Session``
    (verified in backend/app/jobs/): run_dlp_retention_cleanup,
    run_log_archive, run_partition_maintenance.
  * Job de-dup uses ``pg_try_advisory_lock`` — a SESSION-level lock. PgBouncer
    transaction pooling releases the session between statements and would drop
    the lock, so the worker MUST be on a direct, session-mode connection. The
    worker container sets POSTGRES_HOST=postgres (not pgbouncer), so
    ``app.core.db.SessionLocal`` is a direct connection.

Usage: ``python -m worker``
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time

from sqlalchemy import text

from app.core.db import SessionLocal
from app.jobs.dlp_retention import run_dlp_retention_cleanup
from app.jobs.log_archive_job import run_log_archive
from app.jobs.partition_maintenance_job import run_partition_maintenance

log = logging.getLogger("worker")

# Arbitrary, unique-per-job advisory-lock IDs. Session-level — see module
# docstring on why the worker must bypass PgBouncer.
LOCK_DLP_RETENTION = 900001
LOCK_LOG_ARCHIVE = 900002
LOCK_PARTITION = 900003

_DAY = 86400

_shutdown = threading.Event()


def _run_with_lock(job_name: str, job_fn, lock_id: int, interval_s: int) -> None:
    """Loop forever: try a non-blocking advisory lock, run the sync job in a
    single transaction on that connection, release. If another worker holds
    the lock, skip this cycle. Runs once at startup, then every interval_s."""
    while not _shutdown.is_set():
        try:
            with SessionLocal() as session:
                got = session.execute(
                    text("SELECT pg_try_advisory_lock(:id)"), {"id": lock_id}
                ).scalar()
                if got:
                    try:
                        log.info("[%s] acquired lock, running", job_name)
                        job_fn(session)  # jobs take (session, *, <defaults>)
                        session.commit()
                        log.info("[%s] done", job_name)
                    finally:
                        session.execute(
                            text("SELECT pg_advisory_unlock(:id)"), {"id": lock_id}
                        )
                        session.commit()
                else:
                    log.debug("[%s] lock held elsewhere, skipping", job_name)
        except Exception:  # noqa: BLE001 — a failed cycle must not kill the loop
            log.exception("[%s] cycle failed", job_name)

        # Sleep until the next interval, waking early on shutdown.
        _shutdown.wait(timeout=interval_s)


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
    log.info("worker starting")

    jobs = (
        ("dlp_retention", run_dlp_retention_cleanup, LOCK_DLP_RETENTION, _DAY),
        ("log_archive", run_log_archive, LOCK_LOG_ARCHIVE, _DAY),
        ("partition_maintenance", run_partition_maintenance, LOCK_PARTITION, _DAY),
    )
    threads = []
    for name, fn, lock, interval in jobs:
        t = threading.Thread(
            target=_run_with_lock,
            args=(name, fn, lock, interval),
            name=name,
            daemon=True,
        )
        t.start()
        threads.append(t)

    def _stop(*_args) -> None:
        log.info("shutdown signal received")
        _shutdown.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    while not _shutdown.is_set():
        time.sleep(1)

    for t in threads:
        t.join(timeout=10)
    log.info("worker stopped")


if __name__ == "__main__":
    main()
