"""FIX129 A2/A3 — pg_stat_activity sampler.

The 2026-09-22 outage was one session ``idle in transaction`` holding a row
lock on ``portal_users`` while a second session waited on it, for 34
minutes. It was plain to see in ``pg_stat_activity`` the whole time; nothing
looked. This module looks every ``pg_activity_sample_seconds``:

* gauges on /metrics (``pg_idle_in_transaction_sessions``,
  ``pg_stuck_transaction_age_seconds``, …);
* the last sample kept in memory for ``/ready`` (which goes red at
  ``pg_stuck_transaction_seconds``) and for the ``stuck_transaction`` alert
  rule (``alert_evaluator``) — neither of them touches the database again;
* a ``pg_stuck_transaction`` warning naming pid, application_name, age and
  the head of the query, so the next incident report writes itself.

The query only reads ``pg_stat_activity`` for the current database and
skips itself. On a non-Postgres bind (sqlite tests) it returns an empty
sample.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import structlog
from sqlalchemy import text

logger = structlog.get_logger(__name__)

_QUERY = text(
    """
    SELECT pid,
           state,
           coalesce(application_name, '')                    AS application_name,
           coalesce(wait_event_type, '')                     AS wait_event_type,
           extract(epoch FROM (now() - xact_start))          AS xact_age_s,
           left(coalesce(query, ''), 120)                    AS query
    FROM pg_stat_activity
    WHERE datname = current_database()
      AND backend_type = 'client backend'
      AND pid <> pg_backend_pid()
      AND xact_start IS NOT NULL
      AND (state = 'idle in transaction' OR wait_event_type = 'Lock')
    ORDER BY xact_start
    LIMIT 20
    """
)


@dataclass
class ActivitySample:
    at: float
    idle_in_transaction: int = 0
    idle_in_transaction_max_age_s: float = 0.0
    lock_waiters: int = 0
    lock_wait_max_age_s: float = 0.0
    worst: list[dict] = field(default_factory=list)
    error: str | None = None

    @property
    def stuck_age_s(self) -> float:
        """Age of the oldest transaction that is idle or blocked on a lock."""
        return max(self.idle_in_transaction_max_age_s, self.lock_wait_max_age_s)

    def as_dict(self) -> dict:
        d = {
            "idle_in_transaction": self.idle_in_transaction,
            "idle_in_transaction_max_age_s": round(self.idle_in_transaction_max_age_s, 1),
            "lock_waiters": self.lock_waiters,
            "lock_wait_max_age_s": round(self.lock_wait_max_age_s, 1),
            "stuck_age_s": round(self.stuck_age_s, 1),
            "sample_age_s": round(max(0.0, time.time() - self.at), 1),
        }
        if self.error:
            d["error"] = self.error
        return d


_last: ActivitySample | None = None


def last_sample() -> ActivitySample | None:
    return _last


def _set_last(sample: ActivitySample) -> None:
    global _last
    _last = sample


def stuck_age_seconds() -> float:
    """What the alert rule reads: 0 when nothing has been sampled yet."""
    return _last.stuck_age_s if _last is not None else 0.0


def sample_once(session) -> ActivitySample:
    """One synchronous read of pg_stat_activity on ``session`` (sync)."""
    sample = ActivitySample(at=time.time())
    try:
        if session.get_bind().dialect.name != "postgresql":
            return sample
        rows = session.execute(_QUERY).mappings().all()
    except Exception as exc:  # noqa: BLE001 — the sampler never raises
        sample.error = f"{type(exc).__name__}: {str(exc)[:120]}"
        return sample
    for r in rows:
        age = float(r["xact_age_s"] or 0.0)
        entry = {
            "pid": int(r["pid"]),
            "state": r["state"],
            "application_name": r["application_name"],
            "wait": r["wait_event_type"],
            "xact_age_s": round(age, 1),
            "query": r["query"],
        }
        if r["state"] == "idle in transaction":
            sample.idle_in_transaction += 1
            sample.idle_in_transaction_max_age_s = max(sample.idle_in_transaction_max_age_s, age)
        if r["wait_event_type"] == "Lock":
            sample.lock_waiters += 1
            sample.lock_wait_max_age_s = max(sample.lock_wait_max_age_s, age)
        sample.worst.append(entry)
    return sample


def record_sample(sample: ActivitySample, *, stuck_after_s: float) -> bool:
    """Publish a sample to the gauges + module state; log when stuck.

    Returns True when the sample is over the stuck threshold.
    """
    from app.observability.metrics import (
        PG_IDLE_IN_TRANSACTION_MAX_AGE_SECONDS,
        PG_IDLE_IN_TRANSACTION_SESSIONS,
        PG_LOCK_WAITERS,
        PG_STUCK_TRANSACTION_AGE_SECONDS,
    )

    _set_last(sample)
    PG_IDLE_IN_TRANSACTION_SESSIONS.set(sample.idle_in_transaction)
    PG_IDLE_IN_TRANSACTION_MAX_AGE_SECONDS.set(sample.idle_in_transaction_max_age_s)
    PG_LOCK_WAITERS.set(sample.lock_waiters)
    PG_STUCK_TRANSACTION_AGE_SECONDS.set(sample.stuck_age_s)
    if sample.error:
        logger.warning("pg_activity_sample_failed", error=sample.error)
        return False
    stuck = sample.stuck_age_s >= stuck_after_s
    if stuck:
        logger.warning(
            "pg_stuck_transaction",
            stuck_age_s=round(sample.stuck_age_s, 1),
            stuck_after_s=stuck_after_s,
            idle_in_transaction=sample.idle_in_transaction,
            lock_waiters=sample.lock_waiters,
            sessions=sample.worst[:5],
        )
    return stuck


async def sampler_loop(session_factory, *, interval_s: float, stuck_after_s: float) -> None:
    """Background task: sample forever, never raise (except cancellation)."""
    await asyncio.sleep(5)
    while True:
        try:
            session = session_factory()
            try:
                sample = await asyncio.to_thread(sample_once, session)
            finally:
                try:
                    session.rollback()
                    session.close()
                except Exception:  # noqa: BLE001
                    pass
            record_sample(sample, stuck_after_s=stuck_after_s)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — telemetry never breaks the app
            logger.warning("pg_activity_sampler_error", error=str(exc)[:200])
        await asyncio.sleep(interval_s)
