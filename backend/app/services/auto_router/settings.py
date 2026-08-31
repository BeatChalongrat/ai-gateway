"""FIX35 Part C — where the tier ceiling comes from, cheaply.

    request   routing_hints.tier          (per turn; chat-edge sends the
                                          conversation's routing_tier)
    tenant    tenants.auto_routing_tier   (migration 079; '' = not set)
    deploy    system_settings
              auto_routing_tier_default   (migration 079 seeds 'high')

``effective_ceiling`` (tiers.py) takes the most specific valid one and never
lets a lower layer raise a higher layer's ceiling. Both DB reads are
memoised for ``_TTL_SECONDS`` and read through a short sync session exactly
like routing_settings (the hot path holds an AsyncSession); every failure
path returns '' so the deployment default / hardcoded 'high' applies —
a settings read must never make routing worse than the shipped default.
"""

from __future__ import annotations

import threading
import time

import structlog

log = structlog.get_logger(__name__)

KEY_TIER_DEFAULT = "auto_routing_tier_default"
HARDCODED_DEFAULT = "high"
_TTL_SECONDS = 30.0

_lock = threading.Lock()
_deploy: tuple[str, float] | None = None
_tenants: dict[str, tuple[str, float]] = {}


def clear_cache() -> None:
    global _deploy
    with _lock:
        _deploy = None
        _tenants.clear()


def _read_setting(key: str) -> str:
    from app.core.db import SessionLocal
    from app.models.system_setting import SystemSettingORM

    with SessionLocal() as s:
        row = s.get(SystemSettingORM, key)
        return (row.value or "").strip() if row is not None else ""


def _read_tenant_tier(tenant_id: str) -> str:
    from app.core.db import SessionLocal
    from app.models.tenant import TenantORM

    with SessionLocal() as s:
        row = s.get(TenantORM, tenant_id)
        return (getattr(row, "auto_routing_tier", "") or "").strip() if row is not None else ""


def deployment_tier_default() -> str:
    """system_settings.auto_routing_tier_default, cached; '' on any failure."""
    global _deploy
    now = time.monotonic()
    with _lock:
        if _deploy is not None and now - _deploy[1] < _TTL_SECONDS:
            return _deploy[0]
    try:
        value = _read_setting(KEY_TIER_DEFAULT)
    except Exception as exc:  # noqa: BLE001
        log.warning("auto_tier_default_read_failed", error=str(exc)[:200])
        value = ""
    with _lock:
        _deploy = (value, now)
    return value


def tenant_tier(tenant_id: str | None) -> str:
    """tenants.auto_routing_tier, cached per tenant; '' when unset / failure."""
    if not tenant_id:
        return ""
    now = time.monotonic()
    with _lock:
        hit = _tenants.get(tenant_id)
        if hit is not None and now - hit[1] < _TTL_SECONDS:
            return hit[0]
    try:
        value = _read_tenant_tier(tenant_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("auto_tier_tenant_read_failed", tenant_id=tenant_id, error=str(exc)[:200])
        value = ""
    with _lock:
        _tenants[tenant_id] = (value, now)
    return value


# ---------------------------------------------------------------------------
# Async variants — read through the REQUEST's own AsyncSession so the hot
# path opens no second connection (and a single-connection test database
# is never wedged). Same caches, same '' on failure.
# ---------------------------------------------------------------------------

async def deployment_tier_default_async(session) -> str:
    global _deploy
    now = time.monotonic()
    with _lock:
        if _deploy is not None and now - _deploy[1] < _TTL_SECONDS:
            return _deploy[0]
    try:
        from app.models.system_setting import SystemSettingORM

        row = await session.get(SystemSettingORM, KEY_TIER_DEFAULT)
        value = (row.value or "").strip() if row is not None else ""
    except Exception as exc:  # noqa: BLE001
        log.warning("auto_tier_default_read_failed", error=str(exc)[:200])
        value = ""
    with _lock:
        _deploy = (value, now)
    return value


async def tenant_tier_async(tenant_id: str | None, session) -> str:
    if not tenant_id:
        return ""
    now = time.monotonic()
    with _lock:
        hit = _tenants.get(tenant_id)
        if hit is not None and now - hit[1] < _TTL_SECONDS:
            return hit[0]
    try:
        from app.models.tenant import TenantORM

        row = await session.get(TenantORM, tenant_id)
        value = (getattr(row, "auto_routing_tier", "") or "").strip() if row is not None else ""
    except Exception as exc:  # noqa: BLE001
        log.warning("auto_tier_tenant_read_failed", tenant_id=tenant_id, error=str(exc)[:200])
        value = ""
    with _lock:
        _tenants[tenant_id] = (value, now)
    return value
