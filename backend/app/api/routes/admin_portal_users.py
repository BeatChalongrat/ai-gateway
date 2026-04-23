"""Admin view over portal users — list, inspect, top-up, suspend, activate."""

from __future__ import annotations

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


router = APIRouter(dependencies=[Depends(require_admin_session)])


class TopupRequest(BaseModel):
    amount: int = Field(..., gt=0)
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
        "email_verified": bool(user.email_verified),
        "created_at": user.created_at,
        "updated_at": user.updated_at,
    }


@router.get("")
def list_users(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    search: str | None = Query(default=None),
    status: str | None = Query(default=None),
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


@router.get("/{user_id}")
def get_user(
    user_id: str,
    session: Session = Depends(get_db_session),
):
    user = session.get(PortalUserORM, user_id)
    if user is None:
        raise NotFoundError("Portal user not found.", code="user_not_found")

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
    user = session.get(PortalUserORM, user_id)
    if user is None:
        raise NotFoundError("Portal user not found.", code="user_not_found")

    admin_label = auth.key_prefix or auth.key_id
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
    return {
        "user": _user_to_dict(user),
        "transaction": {
            "id": tx.id,
            "amount": int(tx.amount),
            "balance_after": int(tx.balance_after),
            "type": tx.type,
            "description": tx.description,
            "created_by": tx.created_by,
            "created_at": tx.created_at,
        },
    }


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
