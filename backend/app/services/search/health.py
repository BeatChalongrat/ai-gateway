"""Search provider health monitoring.

A background task probes every enabled provider every 5 minutes with a
lightweight neutral query, updates the provider row's health/latency/
uptime in the DB, and emits structured outage/recovery logs on status
transitions. The fallback chain (Phase 2) already skips providers marked
'down', so a flapping provider is routed around between probes.

Kept separate from SearchService (Phases 2-3 are frozen): this module
only READS the service's helpers (get_adapter, decode_key, _uptime) and
writes the health columns itself.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.search_provider import SearchProviderORM
from app.services.search.providers import get_adapter
from app.services.search.service import SearchService, decode_key

logger = structlog.get_logger(__name__)

# Hardcoded per the task (not configurable yet).
HEALTH_CHECK_INTERVAL_S = 300  # 5 minutes
HEALTH_CHECK_TIMEOUT_S = 8.0
_DEGRADED_LATENCY_MS = 5000
_HEALTHY = ("healthy", "degraded", "unknown")


async def check_provider_health(
    session: AsyncSession, cfg: SearchProviderORM
) -> str:
    """Probe one provider and persist its health. Returns the new status.

    On a status transition emits a structured log: a healthy->down
    transition is an outage; a down->healthy(/degraded) transition is a
    recovery. The probe query is neutral and never touches user data.
    """
    was_status = cfg.health_status
    now = datetime.now(timezone.utc)
    start = time.monotonic()
    try:
        adapter = get_adapter(cfg.provider, decode_key(cfg.api_key_encrypted))
        await asyncio.wait_for(
            adapter.search("latest news today", max_results=1),
            timeout=HEALTH_CHECK_TIMEOUT_S,
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        new_status = "degraded" if latency_ms > _DEGRADED_LATENCY_MS else "healthy"

        cfg.total_queries = int(cfg.total_queries or 0) + 1
        cfg.health_status = new_status
        cfg.last_health_check = now
        prev = cfg.avg_latency_ms
        # Rolling average weighted toward history (0.8 old / 0.2 new).
        cfg.avg_latency_ms = (
            latency_ms if prev is None else int(prev * 0.8 + latency_ms * 0.2)
        )
        cfg.uptime_pct = SearchService._uptime(cfg)
        await session.flush()

        if was_status == "down":
            logger.info(
                "search_provider_recovered",
                provider=cfg.provider,
                tenant_id=cfg.tenant_id,
                display_name=cfg.display_name,
                downtime_minutes=_downtime_minutes(cfg.last_failure_at, now),
                new_status=new_status,
            )
        return new_status

    except Exception as e:  # noqa: BLE001 — any failure marks the provider down
        # total_queries counts ALL attempts (matches Phase 2's
        # _record_failure), so _uptime = (attempts - failures) / attempts.
        cfg.total_queries = int(cfg.total_queries or 0) + 1
        cfg.total_failures = int(cfg.total_failures or 0) + 1
        cfg.health_status = "down"
        cfg.last_health_check = now
        cfg.last_failure_at = now
        cfg.last_failure_reason = str(e)[:500]
        cfg.uptime_pct = SearchService._uptime(cfg)
        await session.flush()

        # New outage: a provider that was up (or never checked) is now down.
        if was_status in _HEALTHY:
            logger.error(
                "search_provider_outage_detected",
                provider=cfg.provider,
                tenant_id=cfg.tenant_id,
                display_name=cfg.display_name,
                error=str(e)[:200],
                was_status=was_status,
            )
        return "down"


async def _enabled_providers(
    session: AsyncSession, tenant_id: str | None = None
) -> list[SearchProviderORM]:
    stmt = select(SearchProviderORM).where(SearchProviderORM.is_enabled.is_(True))
    if tenant_id is not None:
        stmt = stmt.where(SearchProviderORM.tenant_id == tenant_id)
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


async def check_all_providers_health(session: AsyncSession) -> int:
    """Probe every enabled provider across all tenants. Returns the count
    checked. Each provider is isolated — one provider's failure to even
    build an adapter doesn't abort the sweep."""
    providers = await _enabled_providers(session)
    for cfg in providers:
        try:
            await check_provider_health(session, cfg)
        except Exception as exc:  # noqa: BLE001 — never abort the sweep
            logger.warning(
                "search_health_check_error",
                provider=cfg.provider,
                tenant_id=cfg.tenant_id,
                error=str(exc)[:200],
            )
    logger.info(
        "search_health_check_completed",
        providers_checked=len(providers),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    return len(providers)


async def check_tenant_providers_health(
    session: AsyncSession, tenant_id: str
) -> list[SearchProviderORM]:
    """Probe a single tenant's enabled providers (admin 'Check Now')."""
    providers = await _enabled_providers(session, tenant_id)
    for cfg in providers:
        try:
            await check_provider_health(session, cfg)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "search_health_check_error",
                provider=cfg.provider,
                tenant_id=tenant_id,
                error=str(exc)[:200],
            )
    return providers


def _downtime_minutes(last_failure_at, now: datetime) -> int:
    if last_failure_at is None:
        return 0
    try:
        delta = now - last_failure_at
        return max(0, int(delta.total_seconds() // 60))
    except (TypeError, ValueError):
        return 0


async def search_health_monitor_loop() -> None:
    """Background task: probe all enabled providers every 5 minutes.

    Started from the app lifespan. Uses its own async session per sweep
    and commits so health is durable; a sweep failure is logged and the
    loop continues.
    """
    from app.core.db import get_async_session_factory

    await asyncio.sleep(25)  # let the app settle (after the other sweeps)
    while True:
        try:
            factory = get_async_session_factory()
            async with factory() as session:
                try:
                    await check_all_providers_health(session)
                    await session.commit()
                except Exception:  # noqa: BLE001
                    await session.rollback()
                    raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"[search-health] sweep failed: {exc}", flush=True)
        await asyncio.sleep(HEALTH_CHECK_INTERVAL_S)
