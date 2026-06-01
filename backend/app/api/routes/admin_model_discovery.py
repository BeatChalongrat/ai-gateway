"""Admin endpoints for the DB-backed model registry.

All routes require ``require_admin_session`` (mirrors the rest of
``/admin/*``). Mutating endpoints (POST / PATCH / DELETE) flip
``is_manual_override`` to ``True`` on the affected row so the
background discovery sync leaves admin edits alone.

Endpoints (mounted at ``/admin/models``):
  GET  /discovery/status      — last sync + per-provider counts
  POST /discovery/sync        — trigger sync (optionally per-provider)
  GET  /                      — paginated row listing with filters
  PATCH /{model_id}           — override any subset of fields
  DELETE /{model_id}/override — clear the manual-override flag
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_db_session, require_admin_session
from app.core.exceptions import NotFoundError, ValidationError
from app.models.model_config import ModelConfigORM
from app.services.discovery.discovery_service import ModelDiscoveryService
from app.services.discovery.provider_discovery import PROVIDER_DISCOVERY


router = APIRouter(dependencies=[Depends(require_admin_session)])


# Module-level singleton — shared with the APScheduler-driven path so
# the litellm pricing cache is reused between scheduled syncs and
# admin-triggered ones.
_discovery_service = ModelDiscoveryService()


# ---------------------------------------------------------------------------
# Pydantic shapes
# ---------------------------------------------------------------------------

class SyncBody(BaseModel):
    provider: Optional[str] = Field(
        default=None,
        description=(
            "Provider id to sync. Omit to sync all providers. Must match "
            "an entry in the discovery dispatch table."
        ),
    )


class ModelConfigUpdate(BaseModel):
    """Subset of fields an admin can override.

    Setting *any* of these flips ``is_manual_override=True`` so the
    next sync skips the row. To re-enable auto-updates, call
    ``DELETE /admin/models/{id}/override``.
    """

    supports_streaming: Optional[bool] = None
    supports_tools: Optional[bool] = None
    supports_vision: Optional[bool] = None
    # Reasoning / thinking capability (migration 045). All three are
    # admin-overridable like the other capability flags.
    supports_reasoning: Optional[bool] = None
    reasoning_strategy: Optional[str] = None
    default_reasoning_mode: Optional[str] = None
    max_context_tokens: Optional[int] = None
    cost_tier: Optional[str] = None
    latency_tier: Optional[str] = None
    cost_per_input_token: Optional[float] = None
    cost_per_output_token: Optional[float] = None
    is_active: Optional[bool] = None


def _serialize_row(row: ModelConfigORM) -> dict:
    return {
        "id": row.id,
        "provider": row.provider,
        "model": row.model,
        "supports_streaming": row.supports_streaming,
        "supports_tools": row.supports_tools,
        "supports_vision": row.supports_vision,
        "supports_reasoning": getattr(row, "supports_reasoning", False),
        "reasoning_strategy": getattr(row, "reasoning_strategy", "none"),
        "default_reasoning_mode": getattr(row, "default_reasoning_mode", "auto"),
        "max_context_tokens": row.max_context_tokens,
        "cost_tier": row.cost_tier,
        "latency_tier": row.latency_tier,
        "cost_per_input_token": row.cost_per_input_token,
        "cost_per_output_token": row.cost_per_output_token,
        "source": row.source,
        "is_manual_override": row.is_manual_override,
        "is_active": row.is_active,
        "last_synced_at": row.last_synced_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


# ---------------------------------------------------------------------------
# GET /admin/models/discovery/status
# ---------------------------------------------------------------------------

@router.get("/discovery/status")
def discovery_status(session: Session = Depends(get_db_session)):
    """Per-provider model count + most recent sync timestamp.

    Counts ``is_active=True`` rows so disabled / soft-deleted models
    don't inflate the totals. ``last_synced_at`` is the max across
    all rows globally — APScheduler logs the per-job timestamp;
    this endpoint exposes the DB's view of "when was last sync".
    """
    per_provider = (
        session.execute(
            select(
                ModelConfigORM.provider,
                func.count(ModelConfigORM.id),
            )
            .where(ModelConfigORM.is_active.is_(True))
            .group_by(ModelConfigORM.provider)
            .order_by(ModelConfigORM.provider)
        )
        .all()
    )
    last_sync_at = session.execute(
        select(func.max(ModelConfigORM.last_synced_at))
    ).scalar_one_or_none()
    total_active = session.execute(
        select(func.count(ModelConfigORM.id)).where(
            ModelConfigORM.is_active.is_(True)
        )
    ).scalar_one()
    total_overrides = session.execute(
        select(func.count(ModelConfigORM.id)).where(
            ModelConfigORM.is_manual_override.is_(True)
        )
    ).scalar_one()

    return {
        "last_synced_at": last_sync_at,
        "total_active": total_active,
        "total_overrides": total_overrides,
        "per_provider": [
            {"provider": provider, "active_count": count}
            for provider, count in per_provider
        ],
        "known_providers": sorted(PROVIDER_DISCOVERY.keys()),
    }


# ---------------------------------------------------------------------------
# POST /admin/models/discovery/sync
# ---------------------------------------------------------------------------

@router.post("/discovery/sync")
async def discovery_sync(
    body: SyncBody | None = None,
    session: Session = Depends(get_db_session),
):
    """Trigger a sync immediately and return the result inline.

    Synchronous response — for a 13-provider sweep with the default
    15s/provider timeout the worst case is bounded. If that becomes
    too long we'll switch to job-id polling; today the simpler
    inline response is enough.
    """
    provider = (body.provider if body else None)
    if provider is not None and provider not in PROVIDER_DISCOVERY:
        raise ValidationError(
            f"Unknown provider '{provider}'.",
            details={"known": sorted(PROVIDER_DISCOVERY.keys())},
        )

    if provider is None:
        result = await _discovery_service.sync_all(session)
    else:
        result = await _discovery_service.sync_provider(provider, session)
    return result.to_dict()


# ---------------------------------------------------------------------------
# POST /admin/models/{model_id}/test  — live model health probe (C2)
# ---------------------------------------------------------------------------

@router.post("/{model_id}/test")
async def test_model(
    model_id: int,
    session: Session = Depends(get_db_session),
):
    """Fire a 1-token completion at the model and report health.

    Backs the admin UI "Test" button. Unlike the provider-key test
    (which only validates the credential), this proves the specific
    model id is actually servable right now. Never 500s on an upstream
    failure — the failure is the result.
    """
    row = session.get(ModelConfigORM, model_id)
    if row is None:
        raise NotFoundError(
            f"Model config #{model_id} not found.",
            details={"model_id": model_id},
        )
    from app.services.model_health import check_model_health

    result = await check_model_health(session, row.provider, row.model)
    return {
        "model_id": model_id,
        "provider": row.provider,
        "model": row.model,
        **result,
    }


# ---------------------------------------------------------------------------
# GET /admin/models
# ---------------------------------------------------------------------------

@router.get("")
def list_model_configs(
    provider: Optional[str] = Query(default=None),
    is_active: Optional[bool] = Query(default=None),
    source: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db_session),
):
    """Paginated listing with optional filters."""
    stmt = select(ModelConfigORM)
    if provider:
        stmt = stmt.where(ModelConfigORM.provider == provider)
    if is_active is not None:
        stmt = stmt.where(ModelConfigORM.is_active.is_(is_active))
    if source:
        stmt = stmt.where(ModelConfigORM.source == source)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(
            or_(
                ModelConfigORM.model.ilike(like),
                ModelConfigORM.provider.ilike(like),
            )
        )

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = session.execute(count_stmt).scalar_one()

    stmt = (
        stmt.order_by(
            ModelConfigORM.provider.asc(),
            ModelConfigORM.model.asc(),
        )
        .limit(limit)
        .offset(offset)
    )
    rows = session.execute(stmt).scalars().all()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_serialize_row(r) for r in rows],
    }


# ---------------------------------------------------------------------------
# PATCH /admin/models/{model_id}
# ---------------------------------------------------------------------------

@router.patch("/{model_id}")
async def update_model(
    model_id: int,
    body: ModelConfigUpdate,
    session: Session = Depends(get_db_session),
):
    """Override fields on a single model row.

    Any field set in the body is written and
    ``is_manual_override=True`` is forced — this is the contract that
    keeps the discovery sync from clobbering admin edits.
    """
    row = session.get(ModelConfigORM, model_id)
    if row is None:
        raise NotFoundError(
            f"Model config #{model_id} not found.",
            details={"model_id": model_id},
        )

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise ValidationError("At least one field must be provided.")

    for key, value in updates.items():
        setattr(row, key, value)
    row.is_manual_override = True
    row.source = "manual"

    session.flush()
    session.commit()

    # Refresh the in-memory registry so the override takes effect on
    # the next routing call rather than waiting for the next sync.
    try:
        from app.services.providers.model_registry import _registry
        await _registry.reload(session)
    except Exception:
        # Reload failure shouldn't block the write — operator will see
        # the change after the next sync or restart.
        pass

    return _serialize_row(row)


# ---------------------------------------------------------------------------
# DELETE /admin/models/{model_id}/override
# ---------------------------------------------------------------------------

@router.delete("/{model_id}/override")
async def clear_override(
    model_id: int,
    session: Session = Depends(get_db_session),
):
    """Allow the discovery sync to manage this row again.

    Does NOT delete the row or revert any field values — the next
    sync run will update them from upstream / litellm. To re-protect
    after clearing, an admin can PATCH the row again.
    """
    row = session.get(ModelConfigORM, model_id)
    if row is None:
        raise NotFoundError(
            f"Model config #{model_id} not found.",
            details={"model_id": model_id},
        )
    if not row.is_manual_override:
        # Already unlocked — return the current state rather than 4xx.
        return _serialize_row(row)

    row.is_manual_override = False
    row.source = "auto"
    session.flush()
    session.commit()
    return _serialize_row(row)
