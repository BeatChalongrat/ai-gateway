"""FIX131 — every worker's model registry follows ``model_configs``.

The registry (``model_registry._registry``) is a per-process copy of the
active ``model_configs`` rows: routing, ``/v1/models``, capability flags and
the prices usage billing reads. Every write path reloads it — in the ONE
worker that handled the write (admin PATCH, "sync now", the capability test,
discovery). With one process that was the whole backend; with N gunicorn
workers the other N-1 kept the old rows (and billed at old prices) until
their own daily discovery run or a restart.

Each worker now checks a fingerprint of the active rows every
``interval_s`` and reloads when it changed. Polling the table instead of
announcing reloads needs no cooperation from the writers (a writer that
forgets to announce, or raw SQL, is still seen) and survives a missed Redis
message. On Postgres the fingerprint is an md5 over the rows' full text, so
any column change counts; elsewhere (sqlite tests) count + newest updated_at.
"""

from __future__ import annotations

import asyncio

import structlog
from sqlalchemy import text

logger = structlog.get_logger(__name__)

INTERVAL_S = 15.0

_PG_FINGERPRINT = text(
    "SELECT md5(coalesce(string_agg(m::text, '|' ORDER BY m.id), '')) "
    "FROM model_configs m WHERE m.is_active"
)
_GENERIC_FINGERPRINT = text(
    "SELECT count(*), max(updated_at) FROM model_configs WHERE is_active"
)


def fingerprint(session) -> str:
    """A string that changes whenever the active rows change."""
    if session.get_bind().dialect.name == "postgresql":
        return str(session.execute(_PG_FINGERPRINT).scalar())
    row = session.execute(_GENERIC_FINGERPRINT).one()
    return f"{row[0]}:{row[1]}"


async def sync_loop(session_factory, *, reload, interval_s: float = INTERVAL_S,
                    fingerprint_fn=fingerprint) -> None:
    """Reload the registry whenever the fingerprint moves. Never raises.

    ``reload(session)`` is the registry's async reload. The first pass
    records the fingerprint and reloads once, which also covers a change
    that landed between the lifespan's initial reload and this loop.
    """
    last: str | None = None
    while True:
        try:
            session = session_factory()
            try:
                current = await asyncio.to_thread(fingerprint_fn, session)
                if current != last:
                    await reload(session)
                    if last is not None:
                        logger.info("model_registry_followed_change")
                    last = current
            finally:
                try:
                    session.rollback()
                    session.close()
                except Exception:  # noqa: BLE001
                    pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a failed pass keeps the old snapshot
            logger.warning("model_registry_sync_failed", error=str(exc)[:200])
        await asyncio.sleep(interval_s)
