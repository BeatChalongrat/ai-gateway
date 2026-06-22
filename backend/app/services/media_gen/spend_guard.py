"""Spend guards for real (Replicate) media generation — Sprint 2B.

Three independent, NON-BYPASSABLE cost guards:

- **L1 session budget** — a global Redis counter
  ``replicate:spend:session:{date}`` capped at
  ``settings.replicate_test_budget_usd``. The adapter reserves the estimate
  ATOMICALLY (Lua check-and-increment) BEFORE any provider call and refunds
  it if the submit fails. One uncaught retry loop therefore cannot exceed
  the session cap.
- **L2 tenant daily cap** — a per-``(tenant, date)`` counter capped at the
  tenant's ``max_daily_spend_usd`` (migration 058). Checked before a job is
  inserted; committed when a job completes successfully.
- **L3 per-job confirmation** — the ``requires_confirmation`` flag (Sprint
  2A, $1.00 threshold), enforced at the route/UI layer.

There is NO env/admin bypass. Raising a limit means changing
``replicate_test_budget_usd`` (session) or ``tenants.max_daily_spend_usd``
(an audit-trailed DB change).

Counters use ``INCRBYFLOAT`` (cent-scale USD estimates); the authoritative
ledger billing stays in ``credits_for_cost``. Keys expire after 24h.

Lua is used for the atomic L1 reserve/refund (single round-trip, no
TOCTOU). Because ``fakeredis`` has no ``EVAL`` support, the spend-guard
tests run against a real Redis (see tests/integration/test_spend_guard.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from app.core.config import settings
from app.observability.metrics import (
    REPLICATE_BUDGET_BLOCKS_TOTAL,
    REPLICATE_DAILY_SPEND_USD,
    REPLICATE_SESSION_SPEND_USD,
)

_DAY_TTL = 86400

SESSION_KEY = "replicate:spend:session:{date}"
TENANT_KEY = "spend:tenant:{tenant_id}:{date}"

# Atomic check-and-reserve: increments ONLY if the total stays within cap.
# Returns 1 (reserved) or 0 (refused, no mutation).
_CHECK_AND_RESERVE_LUA = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local estimate = tonumber(ARGV[1])
local cap = tonumber(ARGV[2])
if (current + estimate) > cap then
  return 0
end
redis.call('INCRBYFLOAT', KEYS[1], estimate)
redis.call('EXPIRE', KEYS[1], ARGV[3])
return 1
"""

# Atomic decrement-with-clamp-at-zero for refunds.
_RELEASE_LUA = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local amount = tonumber(ARGV[1])
local new = current - amount
if new < 0 then new = 0 end
redis.call('SET', KEYS[1], tostring(new))
redis.call('EXPIRE', KEYS[1], ARGV[2])
return tostring(new)
"""


class SpendCapExceeded(Exception):
    """Raised when an L1 session-budget reservation is refused."""


@dataclass
class CapCheckResult:
    allowed: bool
    current_spend: Decimal
    cap: Decimal
    would_be: Decimal


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _redis(redis: Optional[Any]) -> Any:
    if redis is not None:
        return redis
    from app.services._redis_pool import get_async_redis

    return get_async_redis()


def _dec(v: Any) -> Decimal:
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001
        return Decimal("0")


async def check_and_reserve_session_budget(
    estimate_usd: Decimal, *, redis: Optional[Any] = None
) -> bool:
    """L1 guard. Atomically reserve ``estimate_usd`` against the session cap.

    Returns True (and increments the counter) if it fits, else False (no
    mutation). MUST be the FIRST thing the adapter does in ``submit_job``,
    before any provider HTTP call.
    """
    r = _redis(redis)
    key = SESSION_KEY.format(date=_today())
    cap = settings.replicate_test_budget_usd
    res = await r.eval(
        _CHECK_AND_RESERVE_LUA,
        1,
        key,
        str(float(estimate_usd)),
        str(float(cap)),
        str(_DAY_TTL),
    )
    allowed = int(res) == 1
    if not allowed:
        _safe_inc_block("L1_session")
    else:
        await _refresh_session_gauge(r)
    return allowed


async def release_session_budget(
    estimate_usd: Decimal, *, redis: Optional[Any] = None
) -> None:
    """Refund a reservation (the job failed). Decrement clamped at 0."""
    r = _redis(redis)
    key = SESSION_KEY.format(date=_today())
    await r.eval(_RELEASE_LUA, 1, key, str(float(estimate_usd)), str(_DAY_TTL))
    await _refresh_session_gauge(r)


async def get_session_spend(
    date: Optional[str] = None, *, redis: Optional[Any] = None
) -> Decimal:
    """Current session spend (for monitoring / the Grafana panel)."""
    r = _redis(redis)
    key = SESSION_KEY.format(date=date or _today())
    val = await r.get(key)
    return _dec(val) if val is not None else Decimal("0")


async def check_daily_spend_cap(
    session: Any,
    tenant_id: str,
    estimate_usd: Decimal,
    *,
    redis: Optional[Any] = None,
) -> CapCheckResult:
    """L2 guard. Whether ``today's spend + estimate`` stays within the
    tenant's ``max_daily_spend_usd``. Does NOT mutate (commit happens on
    job completion via :func:`commit_daily_spend`).

    NOTE: extends the plan's 2-arg signature with ``session`` — the cap
    lives on the tenants row and must be read from the DB.
    """
    cap = await _tenant_cap(session, tenant_id)
    r = _redis(redis)
    key = TENANT_KEY.format(tenant_id=tenant_id, date=_today())
    val = await r.get(key)
    current = _dec(val) if val is not None else Decimal("0")
    would_be = current + Decimal(str(estimate_usd))
    allowed = would_be <= cap
    if not allowed:
        _safe_inc_block("L2_tenant_daily")
    return CapCheckResult(
        allowed=allowed, current_spend=current, cap=cap, would_be=would_be
    )


async def commit_daily_spend(
    tenant_id: str, actual_usd: Decimal, *, redis: Optional[Any] = None
) -> None:
    """Add a completed job's actual cost to the tenant's daily counter."""
    r = _redis(redis)
    key = TENANT_KEY.format(tenant_id=tenant_id, date=_today())
    await r.incrbyfloat(key, float(actual_usd))
    await r.expire(key, _DAY_TTL)
    try:
        new = await r.get(key)
        REPLICATE_DAILY_SPEND_USD.labels(str(tenant_id)).set(float(_dec(new)))
    except Exception:  # noqa: BLE001
        pass


async def _tenant_cap(session: Any, tenant_id: str) -> Decimal:
    from app.models.tenant import TenantORM

    default = Decimal("10.00")
    if not tenant_id:
        return default
    try:
        tenant = await session.get(TenantORM, tenant_id)
    except Exception:  # noqa: BLE001 — never let a DB hiccup disable the guard
        return default
    if tenant is None or tenant.max_daily_spend_usd is None:
        return default
    return Decimal(str(tenant.max_daily_spend_usd))


async def _refresh_session_gauge(r: Any) -> None:
    try:
        val = await r.get(SESSION_KEY.format(date=_today()))
        REPLICATE_SESSION_SPEND_USD.set(float(_dec(val)))
    except Exception:  # noqa: BLE001
        pass


def _safe_inc_block(level: str) -> None:
    try:
        REPLICATE_BUDGET_BLOCKS_TOTAL.labels(level).inc()
    except Exception:  # noqa: BLE001
        pass
