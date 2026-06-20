"""Admin API for web-search configuration + provider management.

Routes (mounted under /admin):
  GET    /tenants/{tid}/search-config
  PUT    /tenants/{tid}/search-config
  GET    /tenants/{tid}/search-providers
  POST   /tenants/{tid}/search-providers
  PUT    /tenants/{tid}/search-providers/{pid}
  DELETE /tenants/{tid}/search-providers/{pid}
  POST   /tenants/{tid}/search-providers/{pid}/test
  PUT    /tenants/{tid}/search-providers/reorder
  POST   /tenants/{tid}/search-providers/check-health
  GET    /search-health/summary

Auth: session-cookie admin (require_admin_session) + per-tenant access
check. API keys are AES-256-GCM encrypted at rest (same as LLM provider
keys) and NEVER returned — only a last-4 mask. The test endpoint runs a
neutral probe query and deducts no credits.
"""

from __future__ import annotations

import asyncio
import time
import uuid

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ResolvedApiKey, check_tenant_access, require_admin_session
from app.core.db import get_async_db
from app.core.exceptions import NotFoundError, ValidationError
from app.models.search_provider import SearchProviderORM
from app.models.tenant import TenantORM
from app.services.search.health import check_tenant_providers_health
from app.services.search.providers import SUPPORTED_PROVIDERS, get_adapter
from app.services.search.service import decode_key, encode_key

logger = structlog.get_logger(__name__)
router = APIRouter(dependencies=[Depends(require_admin_session)])


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------

class SearchConfigUpdate(BaseModel):
    search_enabled: bool | None = None
    search_credit_cost: int | None = Field(default=None, ge=0, le=10000)


class ProviderCreate(BaseModel):
    provider: str = Field(..., min_length=1, max_length=50)
    api_key: str = Field(..., min_length=1, max_length=2000)
    display_name: str | None = Field(default=None, max_length=100)
    max_results: int = Field(default=5, ge=1, le=10)


class ProviderUpdate(BaseModel):
    api_key: str | None = Field(default=None, min_length=1, max_length=2000)
    display_name: str | None = Field(default=None, max_length=100)
    is_enabled: bool | None = None
    max_results: int | None = Field(default=None, ge=1, le=10)


class ReorderBody(BaseModel):
    provider_ids: list[str] = Field(..., min_length=1)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _mask(raw: str) -> str:
    """Last-4 mask — never expose the key. Mirrors provider_key masking."""
    if not raw or len(raw) <= 4:
        return "••••"
    return "••••" + raw[-4:]


def _provider_to_dict(p: SearchProviderORM) -> dict:
    return {
        "id": p.id,
        "tenant_id": p.tenant_id,
        "provider": p.provider,
        "display_name": p.display_name,
        "priority": int(p.priority),
        "is_enabled": bool(p.is_enabled),
        "max_results": int(p.max_results),
        "api_key_masked": _mask(_safe_decode(p.api_key_encrypted)),
        "health_status": p.health_status,
        "uptime_pct": float(p.uptime_pct) if p.uptime_pct is not None else None,
        "avg_latency_ms": p.avg_latency_ms,
        "total_queries": int(p.total_queries or 0),
        "total_failures": int(p.total_failures or 0),
        "last_health_check": p.last_health_check.isoformat()
        if p.last_health_check
        else None,
        "last_failure_at": p.last_failure_at.isoformat()
        if p.last_failure_at
        else None,
        "last_failure_reason": p.last_failure_reason,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


def _safe_decode(encoded: str) -> str:
    try:
        return decode_key(encoded)
    except Exception:  # noqa: BLE001 — masking must never raise
        return ""


async def _load_tenant(session: AsyncSession, tid: str) -> TenantORM:
    tenant = await session.get(TenantORM, tid)
    if tenant is None:
        raise NotFoundError(f"Tenant '{tid}' not found.")
    return tenant


async def _providers_for_tenant(
    session: AsyncSession, tid: str
) -> list[SearchProviderORM]:
    rows = (
        await session.execute(
            select(SearchProviderORM)
            .where(SearchProviderORM.tenant_id == tid)
            .order_by(SearchProviderORM.priority.asc())
        )
    ).scalars().all()
    return list(rows)


async def _next_priority(session: AsyncSession, tid: str) -> int:
    cur = (
        await session.execute(
            select(func.max(SearchProviderORM.priority)).where(
                SearchProviderORM.tenant_id == tid
            )
        )
    ).scalar()
    return 0 if cur is None else int(cur) + 1


async def _get_provider(
    session: AsyncSession, tid: str, pid: str
) -> SearchProviderORM:
    p = await session.get(SearchProviderORM, pid)
    if p is None or p.tenant_id != tid:
        raise NotFoundError(f"Search provider '{pid}' not found.")
    return p


async def _reindex_priorities(session: AsyncSession, tid: str) -> None:
    """Compact priorities to 0..n in current order (after a delete)."""
    rows = await _providers_for_tenant(session, tid)
    for i, p in enumerate(rows):
        if int(p.priority) != i:
            p.priority = i
    await session.flush()


# --------------------------------------------------------------------------
# Tenant search config
# --------------------------------------------------------------------------

@router.get("/tenants/{tid}/search-config")
async def get_search_config(
    tid: str,
    auth: ResolvedApiKey = Depends(require_admin_session),
    session: AsyncSession = Depends(get_async_db),
) -> dict:
    check_tenant_access(auth, tid)
    tenant = await _load_tenant(session, tid)
    providers = await _providers_for_tenant(session, tid)
    return {
        "search_enabled": bool(getattr(tenant, "search_enabled", False)),
        "search_credit_cost": int(getattr(tenant, "search_credit_cost", 5)),
        "providers": [_provider_to_dict(p) for p in providers],
        "supported_providers": list(SUPPORTED_PROVIDERS),
    }


@router.put("/tenants/{tid}/search-config")
async def update_search_config(
    tid: str,
    body: SearchConfigUpdate,
    auth: ResolvedApiKey = Depends(require_admin_session),
    session: AsyncSession = Depends(get_async_db),
) -> dict:
    check_tenant_access(auth, tid)
    tenant = await _load_tenant(session, tid)
    if body.search_enabled is not None:
        tenant.search_enabled = body.search_enabled
    if body.search_credit_cost is not None:
        tenant.search_credit_cost = body.search_credit_cost
    await session.flush()
    return {
        "search_enabled": bool(tenant.search_enabled),
        "search_credit_cost": int(tenant.search_credit_cost),
    }


# --------------------------------------------------------------------------
# Provider CRUD
# --------------------------------------------------------------------------

@router.get("/tenants/{tid}/search-providers")
async def list_providers(
    tid: str,
    auth: ResolvedApiKey = Depends(require_admin_session),
    session: AsyncSession = Depends(get_async_db),
) -> list[dict]:
    check_tenant_access(auth, tid)
    return [_provider_to_dict(p) for p in await _providers_for_tenant(session, tid)]


@router.post("/tenants/{tid}/search-providers")
async def create_provider(
    tid: str,
    body: ProviderCreate,
    auth: ResolvedApiKey = Depends(require_admin_session),
    session: AsyncSession = Depends(get_async_db),
) -> dict:
    check_tenant_access(auth, tid)
    await _load_tenant(session, tid)
    if body.provider not in SUPPORTED_PROVIDERS:
        raise ValidationError(
            f"Unsupported provider '{body.provider}'. "
            f"Supported: {', '.join(SUPPORTED_PROVIDERS)}."
        )
    row = SearchProviderORM(
        id=str(uuid.uuid4()),
        tenant_id=tid,
        provider=body.provider,
        display_name=body.display_name,
        api_key_encrypted=encode_key(body.api_key),
        priority=await _next_priority(session, tid),
        max_results=body.max_results,
    )
    session.add(row)
    await session.flush()
    logger.info(
        "search_provider_created",
        tenant_id=tid,
        provider=body.provider,
        provider_id=row.id,
    )
    return _provider_to_dict(row)


@router.put("/tenants/{tid}/search-providers/reorder")
async def reorder_providers(
    tid: str,
    body: ReorderBody,
    auth: ResolvedApiKey = Depends(require_admin_session),
    session: AsyncSession = Depends(get_async_db),
) -> list[dict]:
    check_tenant_access(auth, tid)
    existing = {p.id: p for p in await _providers_for_tenant(session, tid)}
    unknown = [pid for pid in body.provider_ids if pid not in existing]
    if unknown:
        raise ValidationError(f"Unknown provider ids: {', '.join(unknown)}")
    # Two-pass to dodge the UNIQUE(tenant, provider, priority) constraint
    # if two rows share a provider type: bump to a high band first, then
    # assign the final 0..n in the requested order. Any ids not named are
    # appended after, preserving their relative order.
    ordered = list(body.provider_ids) + [
        pid for pid in existing if pid not in body.provider_ids
    ]
    for i, pid in enumerate(ordered):
        existing[pid].priority = 10000 + i
    await session.flush()
    for i, pid in enumerate(ordered):
        existing[pid].priority = i
    await session.flush()
    return [_provider_to_dict(p) for p in await _providers_for_tenant(session, tid)]


@router.put("/tenants/{tid}/search-providers/{pid}")
async def update_provider(
    tid: str,
    pid: str,
    body: ProviderUpdate,
    auth: ResolvedApiKey = Depends(require_admin_session),
    session: AsyncSession = Depends(get_async_db),
) -> dict:
    check_tenant_access(auth, tid)
    p = await _get_provider(session, tid, pid)
    if body.api_key is not None:
        p.api_key_encrypted = encode_key(body.api_key)
        # A new key invalidates the old health verdict.
        p.health_status = "unknown"
    if body.display_name is not None:
        p.display_name = body.display_name
    if body.is_enabled is not None:
        p.is_enabled = body.is_enabled
    if body.max_results is not None:
        p.max_results = body.max_results
    await session.flush()
    return _provider_to_dict(p)


@router.delete(
    "/tenants/{tid}/search-providers/{pid}",
    status_code=204,
    response_model=None,
)
async def delete_provider(
    tid: str,
    pid: str,
    auth: ResolvedApiKey = Depends(require_admin_session),
    session: AsyncSession = Depends(get_async_db),
):
    check_tenant_access(auth, tid)
    p = await _get_provider(session, tid, pid)
    await session.delete(p)
    await session.flush()
    await _reindex_priorities(session, tid)
    logger.info("search_provider_deleted", tenant_id=tid, provider_id=pid)


# --------------------------------------------------------------------------
# Test + health
# --------------------------------------------------------------------------

@router.post("/tenants/{tid}/search-providers/{pid}/test")
async def test_provider(
    tid: str,
    pid: str,
    auth: ResolvedApiKey = Depends(require_admin_session),
    session: AsyncSession = Depends(get_async_db),
) -> dict:
    """Run a neutral probe search to verify the key. No credit deducted."""
    check_tenant_access(auth, tid)
    p = await _get_provider(session, tid, pid)
    start = time.monotonic()
    try:
        adapter = get_adapter(p.provider, decode_key(p.api_key_encrypted))
        results = await asyncio.wait_for(
            adapter.search("latest world news", max_results=2), timeout=10.0
        )
        latency = int((time.monotonic() - start) * 1000)
        return {
            "status": "success",
            "latency_ms": latency,
            "results_count": len(results.results),
        }
    except Exception as e:  # noqa: BLE001 — test result, not a request failure
        return {"status": "error", "error": str(e)[:300]}


@router.post("/tenants/{tid}/search-providers/check-health")
async def check_tenant_health(
    tid: str,
    auth: ResolvedApiKey = Depends(require_admin_session),
    session: AsyncSession = Depends(get_async_db),
) -> list[dict]:
    """Immediate health check for this tenant's enabled providers."""
    check_tenant_access(auth, tid)
    await check_tenant_providers_health(session, tid)
    await session.flush()
    return [_provider_to_dict(p) for p in await _providers_for_tenant(session, tid)]


@router.get("/search-health/summary")
async def search_health_summary(
    auth: ResolvedApiKey = Depends(require_admin_session),
    session: AsyncSession = Depends(get_async_db),
) -> dict:
    """Aggregate health across all tenants (global-admin dashboard)."""
    # Only global admins see the cross-tenant view; tenant admins get
    # their own tenant's providers.
    from app.domain.rbac import Role

    try:
        role = Role[auth.role.upper()]
    except (KeyError, AttributeError):
        role = Role.USER

    stmt = select(SearchProviderORM)
    if role < Role.ADMIN:
        stmt = stmt.where(SearchProviderORM.tenant_id == auth.tenant_id)
    rows = list((await session.execute(stmt)).scalars().all())

    # Friendly tenant names in one batched lookup.
    tenant_ids = {r.tenant_id for r in rows}
    names: dict[str, str] = {}
    if tenant_ids:
        trows = (
            await session.execute(
                select(TenantORM.tenant_id, TenantORM.name).where(
                    TenantORM.tenant_id.in_(tenant_ids)
                )
            )
        ).all()
        names = {tid: name for tid, name in trows}

    counts = {"healthy": 0, "degraded": 0, "down": 0, "unknown": 0, "disabled": 0}
    last_check = None
    providers: list[dict] = []
    for r in rows:
        bucket = "disabled" if not r.is_enabled else (r.health_status or "unknown")
        if bucket not in counts:
            bucket = "unknown"
        counts[bucket] += 1
        if r.last_health_check and (last_check is None or r.last_health_check > last_check):
            last_check = r.last_health_check
        d = _provider_to_dict(r)
        d["tenant_name"] = names.get(r.tenant_id, r.tenant_id)
        providers.append(d)

    return {
        "total_providers": len(rows),
        "healthy": counts["healthy"],
        "degraded": counts["degraded"],
        "down": counts["down"],
        "unknown": counts["unknown"],
        "disabled": counts["disabled"],
        "last_check": last_check.isoformat() if last_check else None,
        "providers": providers,
    }
