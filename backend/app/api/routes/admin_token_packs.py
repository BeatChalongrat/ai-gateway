"""Admin token-pack CRUD.

Delete is soft — flip ``is_active`` to False instead of deleting rows,
so existing credit_transactions with a past ``purchase`` of the pack
still point at a valid row for reporting.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db_session, require_admin_session
from app.core.exceptions import NotFoundError
from app.models.token_pack import TokenPackORM


router = APIRouter(dependencies=[Depends(require_admin_session)])


class PackCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    tokens: int = Field(..., gt=0)
    price_usd: float = Field(..., gt=0)
    description: str | None = Field(default=None, max_length=1000)
    sort_order: int = Field(default=0, ge=0)


class PackUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    tokens: int | None = Field(default=None, gt=0)
    price_usd: float | None = Field(default=None, gt=0)
    description: str | None = Field(default=None, max_length=1000)
    is_active: bool | None = None
    sort_order: int | None = Field(default=None, ge=0)


def _to_dict(p: TokenPackORM) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "tokens": int(p.tokens),
        "price_usd": float(p.price_usd),
        "description": p.description,
        "stripe_price_id": p.stripe_price_id,
        "is_active": bool(p.is_active),
        "sort_order": int(p.sort_order),
        "created_at": p.created_at,
    }


@router.get("")
def list_packs(session: Session = Depends(get_db_session)):
    rows = (
        session.execute(
            select(TokenPackORM).order_by(
                TokenPackORM.sort_order.asc(), TokenPackORM.created_at.asc()
            )
        )
        .scalars()
        .all()
    )
    return [_to_dict(p) for p in rows]


@router.post("")
def create_pack(
    body: PackCreate,
    session: Session = Depends(get_db_session),
):
    pack = TokenPackORM(
        id=str(uuid.uuid4()),
        name=body.name,
        tokens=body.tokens,
        price_usd=Decimal(str(body.price_usd)),
        description=body.description,
        sort_order=body.sort_order,
        is_active=True,
    )
    session.add(pack)
    session.flush()
    return _to_dict(pack)


@router.patch("/{pack_id}")
def update_pack(
    pack_id: str,
    body: PackUpdate,
    session: Session = Depends(get_db_session),
):
    pack = session.get(TokenPackORM, pack_id)
    if pack is None:
        raise NotFoundError("Token pack not found.", code="pack_not_found")

    if body.name is not None:
        pack.name = body.name
    if body.tokens is not None:
        pack.tokens = body.tokens
    if body.price_usd is not None:
        pack.price_usd = Decimal(str(body.price_usd))
    if body.description is not None:
        pack.description = body.description
    if body.is_active is not None:
        pack.is_active = body.is_active
    if body.sort_order is not None:
        pack.sort_order = body.sort_order

    session.flush()
    return _to_dict(pack)


@router.delete("/{pack_id}")
def delete_pack(
    pack_id: str,
    session: Session = Depends(get_db_session),
):
    pack = session.get(TokenPackORM, pack_id)
    if pack is None:
        raise NotFoundError("Token pack not found.", code="pack_not_found")
    pack.is_active = False
    session.flush()
    return {"message": "ok"}
