"""Portal self-service: account info, usage, transactions.

All routes require a portal JWT (``get_portal_user`` dependency).
Queries are scoped to ``portal_user_id = current_user.id`` — a portal
user never sees another user's keys, requests, or transactions.
"""

from __future__ import annotations

import io
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db_session, get_portal_user
from app.api.portal_scope import scope_to_portal_tenant
from app.models.api_key import ApiKeyORM
from app.models.credit_transaction import CreditTransactionORM
from app.models.portal_user import PortalUserORM
from app.models.request_log import RequestLogORM
from app.services.portal import credit_service


router = APIRouter(dependencies=[Depends(get_portal_user)])

# Avatar upload constraints — mirrored in client-side pre-checks.
AVATAR_DIR = Path(__file__).resolve().parent.parent.parent.parent / "uploads" / "avatars"
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
OUTPUT_SIZE = 256
OUTPUT_FORMAT = "WEBP"
OUTPUT_EXT = "webp"


class UpdateProfileRequest(BaseModel):
    display_name: str | None = Field(None, max_length=100)
    # Two-letter locale or similar short tag (we only ship 'en' and 'th'
    # today). Longer values are rejected rather than silently truncated
    # so a bad client never quietly overwrites the stored preference.
    preferred_language: str | None = Field(None, max_length=8)


def _low_credit_threshold(db: Session) -> int:
    raw = credit_service.get_setting(db, "credit_low_threshold", "0") or "0"
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


@router.get("")
@scope_to_portal_tenant
def get_me(
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    from app.models.tenant import TenantORM

    balance = int(user.credit_balance)
    used = int(user.credit_used)
    lifetime = balance + used
    percent_used = (used / lifetime * 100.0) if lifetime > 0 else 0.0
    threshold = _low_credit_threshold(session)

    # tenant_name lookup — the Team page header ("Invite teammates to
    # Globex Inc") and the Settings account block both want the
    # display name. A single cheap join here beats an extra GET.
    tenant_name: str | None = None
    if user.tenant_id:
        tenant = session.get(TenantORM, user.tenant_id)
        if tenant is not None:
            tenant_name = tenant.name

    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "avatar_url": user.avatar_url,
        "plan": user.plan,
        "status": user.status,
        "credit_balance": balance,
        "credit_used": used,
        "credit_percent_used": round(percent_used, 2),
        "is_low_credit": threshold > 0 and balance < threshold,
        "email_verified": bool(user.email_verified),
        # Phase 3 tenancy fields. The UI gates the "Team" menu and
        # owner-only actions on tenant_role.
        "tenant_id": user.tenant_id,
        "tenant_name": tenant_name,
        "tenant_role": user.tenant_role or "member",
        # Transactional-email language + future UI locale. Exposing it
        # on /me lets the Settings page preselect the correct radio
        # without a separate round-trip.
        "preferred_language": user.preferred_language or "en",
        # Linked-account info — null when the user signed up with
        # email/password, "google" / "github" when SSO is active. Used
        # by the Settings "Linked accounts" section to show status.
        "oauth_provider": user.oauth_provider,
        "created_at": user.created_at,
    }


@router.patch("")
@scope_to_portal_tenant
def update_me(
    payload: UpdateProfileRequest,
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    if payload.display_name is not None:
        cleaned = payload.display_name.strip()
        user.display_name = cleaned if cleaned else None

    if payload.preferred_language is not None:
        lang = payload.preferred_language.strip().lower()
        # Allow any short locale tag; transactional-email renderer
        # falls back to 'en' for unknown ones so a future "add ja"
        # needs zero backend change.
        if lang and len(lang) <= 8:
            user.preferred_language = lang

    session.commit()
    session.refresh(user)

    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "avatar_url": user.avatar_url,
        "plan": user.plan,
        "status": user.status,
        "credit_balance": int(user.credit_balance),
        "credit_used": int(user.credit_used),
        "preferred_language": user.preferred_language or "en",
        "tenant_id": user.tenant_id,
        "tenant_role": user.tenant_role or "member",
        "created_at": user.created_at,
    }


@router.post("/avatar")
@scope_to_portal_tenant
async def upload_avatar(
    file: UploadFile = File(...),
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=400, detail="Only JPEG, PNG, or WebP images are allowed."
        )

    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="Image must be under 5 MB.")

    try:
        img = Image.open(io.BytesIO(raw))
        img = img.convert("RGB")
    except Exception:
        raise HTTPException(status_code=400, detail="Image could not be read.")

    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left + side, top + side))
    img = img.resize((OUTPUT_SIZE, OUTPUT_SIZE), Image.LANCZOS)

    # Random token in filename busts the <img> cache when a user replaces
    # their avatar — same DB column, different URL.
    token = uuid.uuid4().hex[:8]
    filename = f"{user.id}_{token}.{OUTPUT_EXT}"
    target_path = AVATAR_DIR / filename

    AVATAR_DIR.mkdir(parents=True, exist_ok=True)

    for old in AVATAR_DIR.glob(f"{user.id}_*"):
        try:
            old.unlink()
        except OSError:
            pass

    img.save(target_path, OUTPUT_FORMAT, quality=88)

    avatar_url = f"/static/avatars/{filename}"
    user.avatar_url = avatar_url
    session.commit()
    session.refresh(user)

    return {"avatar_url": avatar_url}


@router.delete("/avatar")
@scope_to_portal_tenant
def delete_avatar(
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    for old in AVATAR_DIR.glob(f"{user.id}_*"):
        try:
            old.unlink()
        except OSError:
            pass

    user.avatar_url = None
    session.commit()

    return {"avatar_url": None}


@router.get("/usage/summary")
@scope_to_portal_tenant
def get_usage_summary(
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    """Aggregated usage for the caller's tenant.

    Phase 4 Feature 1 — dashboard-friendly roll-up of request_logs.
    Includes:

    * ``totals``: lifetime requests, token count, estimated cost.
    * ``windows``: counts for today, last 7d, last 30d.
    * ``top_models``: top 5 by request count.
    * ``by_project``: per-project request + token totals (joined
      against projects so we can surface a friendly name).

    Tenant-scoped via ``user.tenant_id`` — the @scope_to_portal_tenant
    decorator documents the contract and the boot-check enforces it.
    Orphaned portal users (tenant_id=None, transient state during
    removal/re-invite) get an empty summary.
    """
    from datetime import datetime, timedelta, timezone

    from app.models.project import ProjectORM

    tenant_id = user.tenant_id
    empty: dict = {
        "totals": {
            "requests": 0,
            "tokens": 0,
            "estimated_cost_usd": 0.0,
            "estimated_cost_thb": 0.0,
            "cost_display": "฿0.0000",
        },
        "windows": {"today": 0, "last_7d": 0, "last_30d": 0},
        "top_models": [],
        "by_project": [],
    }
    if not tenant_id:
        return empty

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)
    month_start = now - timedelta(days=30)

    scope = RequestLogORM.tenant_id == tenant_id

    # Lifetime totals
    totals_row = session.execute(
        select(
            func.count(RequestLogORM.request_id),
            func.coalesce(func.sum(RequestLogORM.total_tokens), 0),
            func.coalesce(func.sum(RequestLogORM.estimated_cost_usd), 0.0),
        ).where(scope)
    ).one()

    # Time-window request counts
    def _count_since(since_dt) -> int:
        return int(
            session.execute(
                select(func.count(RequestLogORM.request_id)).where(
                    scope, RequestLogORM.created_at >= since_dt
                )
            ).scalar()
            or 0
        )

    # Top 5 models by request count (descending)
    top_models_rows = session.execute(
        select(
            RequestLogORM.model,
            func.count(RequestLogORM.request_id).label("requests"),
            func.coalesce(func.sum(RequestLogORM.total_tokens), 0).label("tokens"),
        )
        .where(scope)
        .group_by(RequestLogORM.model)
        .order_by(func.count(RequestLogORM.request_id).desc())
        .limit(5)
    ).all()

    # Per-project breakdown with friendly names
    by_project_rows = session.execute(
        select(
            RequestLogORM.project_id,
            func.coalesce(ProjectORM.name, RequestLogORM.project_id).label(
                "project_name"
            ),
            func.count(RequestLogORM.request_id).label("requests"),
            func.coalesce(func.sum(RequestLogORM.total_tokens), 0).label("tokens"),
        )
        .outerjoin(ProjectORM, ProjectORM.project_id == RequestLogORM.project_id)
        .where(scope)
        .group_by(RequestLogORM.project_id, ProjectORM.name)
        .order_by(func.count(RequestLogORM.request_id).desc())
    ).all()

    from app.services.currency_service import currency_service
    _total_usd = float(totals_row[2] or 0.0)
    _cost_info = currency_service.format_cost(_total_usd)
    return {
        "totals": {
            "requests": int(totals_row[0] or 0),
            "tokens": int(totals_row[1] or 0),
            "estimated_cost_usd": _total_usd,
            "estimated_cost_thb": _cost_info["thb"],
            "cost_display": _cost_info["display"],
        },
        "windows": {
            "today": _count_since(today_start),
            "last_7d": _count_since(week_start),
            "last_30d": _count_since(month_start),
        },
        "top_models": [
            {
                "model": row[0],
                "requests": int(row[1]),
                "tokens": int(row[2]),
            }
            for row in top_models_rows
        ],
        "by_project": [
            {
                "project_id": row[0],
                "project_name": row[1],
                "requests": int(row[2]),
                "tokens": int(row[3]),
            }
            for row in by_project_rows
        ],
    }


@router.get("/usage")
@scope_to_portal_tenant
def get_usage(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    """Recent gateway requests made with keys owned by the current user.

    Joins ``request_logs`` back to ``api_keys`` via ``api_key_id`` and
    filters on ``api_keys.portal_user_id``. If the request log rows use
    a different FK name in this codebase (``key_id`` vs ``api_key_id``),
    the join condition adapts to whichever column exists.
    """
    join_col = getattr(RequestLogORM, "api_key_id", None) or getattr(
        RequestLogORM, "key_id", None
    )

    total = 0
    rows: list[RequestLogORM] = []
    if join_col is not None:
        base = (
            select(RequestLogORM)
            .join(ApiKeyORM, join_col == ApiKeyORM.key_id)
            .where(ApiKeyORM.portal_user_id == user.id)
        )
        total = session.execute(
            select(func.count())
            .select_from(ApiKeyORM)
            .join(RequestLogORM, join_col == ApiKeyORM.key_id)
            .where(ApiKeyORM.portal_user_id == user.id)
        ).scalar() or 0
        rows = (
            session.execute(
                base.order_by(RequestLogORM.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            .scalars()
            .all()
        )

    def _to_dict(r: RequestLogORM) -> dict:
        # Use getattr with defaults so this works regardless of which
        # extra columns the request_logs table has in this revision.
        return {
            "id": getattr(r, "id", None) or getattr(r, "request_id", None),
            "model": getattr(r, "model", None),
            "provider": getattr(r, "provider", None),
            "status": getattr(r, "status", None),
            "prompt_tokens": getattr(r, "prompt_tokens", None),
            "completion_tokens": getattr(r, "completion_tokens", None),
            "total_tokens": getattr(r, "total_tokens", None),
            "latency_ms": getattr(r, "latency_ms", None),
            "created_at": getattr(r, "created_at", None),
        }

    return {"requests": [_to_dict(r) for r in rows], "total": int(total)}


@router.get("/transactions")
@scope_to_portal_tenant
def get_transactions(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    total = session.execute(
        select(func.count()).select_from(CreditTransactionORM).where(
            CreditTransactionORM.user_id == user.id
        )
    ).scalar() or 0

    rows = (
        session.execute(
            select(CreditTransactionORM)
            .where(CreditTransactionORM.user_id == user.id)
            .order_by(CreditTransactionORM.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )

    return {
        "transactions": [
            {
                "id": t.id,
                "amount": int(t.amount),
                "balance_after": int(t.balance_after),
                "type": t.type,
                "description": t.description,
                "request_id": t.request_id,
                "created_by": t.created_by,
                "created_at": t.created_at,
            }
            for t in rows
        ],
        "total": int(total),
    }
