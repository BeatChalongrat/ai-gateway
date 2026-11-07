"""Admin view over portal users — list, inspect, top-up, reset, ceiling,
bulk, suspend, activate.

FIX88 — every balance movement goes through
``app.services.portal.credit_service`` and lands as a
``credit_transactions`` row; no handler here writes
``portal_users.credit_balance`` or ``credit_quota`` itself
(``tests/test_portal_credit_fix88_ledger_rule.py`` greps for it).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import ResolvedApiKey, get_db_session, require_admin_session
from app.core.exceptions import NotFoundError, ValidationError
from app.models.api_key import ApiKeyORM
from app.models.credit_transaction import CreditTransactionORM
from app.models.portal_user import PortalUserORM
from app.models.project import ProjectORM
from app.services.portal import credit_service
from app.services.portal.credit_service import CreditCeilingExceeded


router = APIRouter(dependencies=[Depends(require_admin_session)])


class TopupRequest(BaseModel):
    # ``amount`` is optional ONLY when ``fill`` is true (fill to the
    # ceiling); a plain top-up still needs a positive amount.
    amount: int | None = Field(default=None, gt=0)
    fill: bool = False
    description: str | None = Field(default=None, max_length=500)


class ResetRequest(BaseModel):
    description: str | None = Field(default=None, max_length=500)


class QuotaRequest(BaseModel):
    quota: int = Field(..., ge=0)
    description: str | None = Field(default=None, max_length=500)


BulkAction = Literal["topup", "fill", "reset", "quota"]


class BulkRequest(BaseModel):
    # Explicit ids only — no "all users matching a filter". 1..500.
    user_ids: list[str] = Field(..., min_length=1, max_length=500)
    action: BulkAction
    amount: int | None = Field(default=None, gt=0)
    quota: int | None = Field(default=None, ge=0)
    description: str | None = Field(default=None, max_length=500)


class SuspendRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


def _user_to_dict(user: PortalUserORM) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "plan": user.plan,
        "status": user.status,
        "credit_balance": int(user.credit_balance),
        "credit_used": int(user.credit_used),
        "credit_quota": int(user.credit_quota),
        "email_verified": bool(user.email_verified),
        "created_at": user.created_at,
        "updated_at": user.updated_at,
    }


def _tx_to_dict(tx: CreditTransactionORM | None) -> dict | None:
    if tx is None:
        return None
    return {
        "id": tx.id,
        "amount": int(tx.amount),
        "balance_after": int(tx.balance_after),
        "type": tx.type,
        "description": tx.description,
        "created_by": tx.created_by,
        "created_at": tx.created_at,
    }


def _admin_label(auth: ResolvedApiKey) -> str:
    return auth.key_prefix or auth.key_id


def _load_user_or_404(session: Session, user_id: str) -> PortalUserORM:
    user = session.get(PortalUserORM, user_id)
    if user is None:
        raise NotFoundError("Portal user not found.", code="user_not_found")
    return user


@router.get("")
def list_users(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    search: str | None = Query(default=None),
    status: str | None = Query(default=None),
    tenant_id: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
):
    filters = []
    if search:
        like = f"%{search.strip().lower()}%"
        filters.append(
            or_(
                func.lower(PortalUserORM.email).like(like),
                func.lower(PortalUserORM.display_name).like(like),
            )
        )
    if status:
        filters.append(PortalUserORM.status == status)
    # tenant_id scope — used by the admin Add-Member dialog to limit
    # the search to the project's tenant. No cross-tenant membership,
    # so the picker shouldn't even offer users from other tenants.
    if tenant_id:
        filters.append(PortalUserORM.tenant_id == tenant_id)

    where = and_(*filters) if filters else None

    total_stmt = select(func.count()).select_from(PortalUserORM)
    if where is not None:
        total_stmt = total_stmt.where(where)
    total = session.execute(total_stmt).scalar() or 0

    list_stmt = select(PortalUserORM)
    if where is not None:
        list_stmt = list_stmt.where(where)
    users = (
        session.execute(
            list_stmt.order_by(PortalUserORM.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )

    # Per-user project + key counts in bulk.
    ids = [u.id for u in users]
    project_counts: dict[str, int] = {}
    key_counts: dict[str, int] = {}
    if ids:
        project_counts = dict(
            session.execute(
                select(
                    ProjectORM.portal_user_id, func.count(ProjectORM.project_id)
                )
                .where(ProjectORM.portal_user_id.in_(ids))
                .group_by(ProjectORM.portal_user_id)
            ).all()
        )
        key_counts = dict(
            session.execute(
                select(ApiKeyORM.portal_user_id, func.count(ApiKeyORM.key_id))
                .where(ApiKeyORM.portal_user_id.in_(ids))
                .group_by(ApiKeyORM.portal_user_id)
            ).all()
        )

    rows = []
    for u in users:
        d = _user_to_dict(u)
        d["project_count"] = int(project_counts.get(u.id, 0))
        d["key_count"] = int(key_counts.get(u.id, 0))
        rows.append(d)

    return {"users": rows, "total": int(total)}


@router.post("/bulk")
def bulk(
    body: BulkRequest,
    auth: ResolvedApiKey = Depends(require_admin_session),
    session: Session = Depends(get_db_session),
):
    """Apply one credit action to an explicit list of users in ONE DB
    transaction.

    All or nothing for STRUCTURAL errors: an unknown user id (404,
    ``user_not_found``, the id named in ``details``), a missing
    ``amount`` for ``topup`` or ``quota`` for ``quota`` (422) — nothing
    moves for anybody. A per-user DOMAIN refusal (a ``topup`` that
    would exceed that user's ceiling) is reported in that user's
    result as ``ok: false, error: "credit_ceiling"`` with the user
    untouched; the other users' movements stand — an admin topping up
    a class does not lose the whole batch because two members are
    already full.

    Response: ``{results: [{user_id, ok, error?, message?, balance_after,
    quota_after, moved}], total_moved, reduced_count}`` — ``moved`` is
    the signed balance delta for that user (0 when nothing moved),
    ``total_moved`` its signed sum, ``reduced_count`` how many balances
    went DOWN (a ceiling lowered below the balance).
    """
    # Duplicates are collapsed so one user is never moved twice per call.
    ids = list(dict.fromkeys(body.user_ids))

    if body.action == "topup" and body.amount is None:
        raise ValidationError(
            "amount is required for action 'topup'.", code="amount_required"
        )
    if body.action == "quota" and body.quota is None:
        raise ValidationError(
            "quota is required for action 'quota'.", code="quota_required"
        )

    users = (
        session.execute(select(PortalUserORM).where(PortalUserORM.id.in_(ids)))
        .scalars()
        .all()
    )
    by_id = {u.id: u for u in users}
    missing = [i for i in ids if i not in by_id]
    if missing:
        raise NotFoundError(
            f"Portal user not found: {missing[0]}",
            code="user_not_found",
            details={"user_id": missing[0], "missing": missing},
        )

    label = _admin_label(auth)
    results: list[dict] = []
    total_moved = 0
    reduced_count = 0

    for uid in ids:
        user = by_id[uid]
        before = int(user.credit_balance)
        entry: dict = {"user_id": uid, "ok": True}
        try:
            if body.action == "topup":
                credit_service.topup_credits(
                    session,
                    user_id=uid,
                    amount=int(body.amount),
                    created_by=label,
                    description=body.description,
                    type="manual_topup",
                )
            elif body.action in ("fill", "reset"):
                credit_service.fill_to_quota(
                    session,
                    user_id=uid,
                    created_by=label,
                    description=body.description,
                )
            else:  # "quota"
                credit_service.set_quota(
                    session,
                    user_id=uid,
                    new_quota=int(body.quota),
                    created_by=label,
                    description=body.description,
                )
        except CreditCeilingExceeded as exc:
            entry["ok"] = False
            entry["error"] = exc.code
            entry["message"] = exc.message
        session.refresh(user)
        moved = int(user.credit_balance) - before
        entry["balance_after"] = int(user.credit_balance)
        entry["quota_after"] = int(user.credit_quota)
        entry["moved"] = moved
        total_moved += moved
        if moved < 0:
            reduced_count += 1
        results.append(entry)

    return {
        "results": results,
        "total_moved": total_moved,
        "reduced_count": reduced_count,
    }


@router.get("/{user_id}")
def get_user(
    user_id: str,
    session: Session = Depends(get_db_session),
):
    user = _load_user_or_404(session, user_id)

    tx_rows = (
        session.execute(
            select(CreditTransactionORM)
            .where(CreditTransactionORM.user_id == user_id)
            .order_by(CreditTransactionORM.created_at.desc())
            .limit(20)
        )
        .scalars()
        .all()
    )
    data = _user_to_dict(user)
    data["transactions"] = [
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
        for t in tx_rows
    ]
    return data


@router.post("/{user_id}/topup")
def topup(
    user_id: str,
    body: TopupRequest,
    auth: ResolvedApiKey = Depends(require_admin_session),
    session: Session = Depends(get_db_session),
):
    """Manual top-up, capped at the ceiling.

    ``{"amount": N}`` adds N (422 ``credit_ceiling`` naming the room
    left when ``balance + N > quota``; the balance is untouched and no
    ledger row is written). ``{"fill": true}`` fills to the ceiling
    (``amount`` ignored; ``transaction`` is null when already full).
    """
    user = _load_user_or_404(session, user_id)
    admin_label = _admin_label(auth)

    if body.fill:
        tx = credit_service.fill_to_quota(
            session,
            user_id=user.id,
            created_by=admin_label,
            description=body.description,
        )
    else:
        if body.amount is None:
            raise ValidationError(
                "amount is required unless fill is true.",
                code="amount_required",
            )
        tx = credit_service.topup_credits(
            session,
            user_id=user.id,
            amount=body.amount,
            created_by=admin_label,
            description=body.description,
            type="manual_topup",
        )
        if tx is None:
            raise ValidationError("Failed to record top-up.", code="topup_failed")

    session.refresh(user)
    return {"user": _user_to_dict(user), "transaction": _tx_to_dict(tx)}


@router.post("/{user_id}/reset")
def reset(
    user_id: str,
    body: ResetRequest | None = None,
    auth: ResolvedApiKey = Depends(require_admin_session),
    session: Session = Depends(get_db_session),
):
    """Reset to full: balance := quota. ``transaction`` is null when the
    balance was already at the ceiling (nothing written)."""
    user = _load_user_or_404(session, user_id)
    tx = credit_service.fill_to_quota(
        session,
        user_id=user.id,
        created_by=_admin_label(auth),
        description=body.description if body else None,
    )
    session.refresh(user)
    return {"user": _user_to_dict(user), "transaction": _tx_to_dict(tx)}


@router.post("/{user_id}/quota")
def set_quota(
    user_id: str,
    body: QuotaRequest,
    auth: ResolvedApiKey = Depends(require_admin_session),
    session: Session = Depends(get_db_session),
):
    """Change the ceiling: quota := body.quota AND balance += (new − old),
    floored at 0. ``transaction`` is null when the balance did not move
    (same quota, or lowered while the balance was already 0)."""
    user = _load_user_or_404(session, user_id)
    tx = credit_service.set_quota(
        session,
        user_id=user.id,
        new_quota=body.quota,
        created_by=_admin_label(auth),
        description=body.description,
    )
    session.refresh(user)
    return {"user": _user_to_dict(user), "transaction": _tx_to_dict(tx)}


@router.post("/{user_id}/suspend")
def suspend(
    user_id: str,
    body: SuspendRequest,
    session: Session = Depends(get_db_session),
):
    user = session.get(PortalUserORM, user_id)
    if user is None:
        raise NotFoundError("Portal user not found.", code="user_not_found")
    user.status = "suspended"
    session.flush()
    return {"user": _user_to_dict(user)}


@router.post("/{user_id}/activate")
def activate(
    user_id: str,
    session: Session = Depends(get_db_session),
):
    user = session.get(PortalUserORM, user_id)
    if user is None:
        raise NotFoundError("Portal user not found.", code="user_not_found")
    user.status = "active"
    session.flush()
    return {"user": _user_to_dict(user)}
