"""Portal billing: token packs + (stubbed) purchase.

Stripe isn't wired in yet. ``POST /portal/billing/purchase`` returns
HTTP 501 with a pointer to support@sran.ai — the response carries the
selected pack so the UI can show "You tried to buy Pack X".
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db_session, get_portal_user
from app.api.portal_scope import explicit_cross_portal_tenant
from app.core.exceptions import NotFoundError
from app.models.portal_user import PortalUserORM
from app.models.token_pack import TokenPackORM


router = APIRouter(dependencies=[Depends(get_portal_user)])


class PurchaseRequest(BaseModel):
    pack_id: str


@router.get("/packs")
@explicit_cross_portal_tenant(
    reason=(
        "Token pack SKUs are global during Phase 3 pre-C25. "
        "Q1 answer ('tenant-scoped packs') lands in C25 which "
        "replaces this marker with @scope_to_portal_tenant."
    )
)
def list_packs(session: Session = Depends(get_db_session)):
    rows = (
        session.execute(
            select(TokenPackORM)
            .where(TokenPackORM.is_active == True)  # noqa: E712
            .order_by(TokenPackORM.sort_order.asc(), TokenPackORM.created_at.asc())
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": p.id,
            "name": p.name,
            "tokens": int(p.tokens),
            "price_usd": float(p.price_usd),
            "description": p.description,
            "sort_order": int(p.sort_order),
        }
        for p in rows
    ]


@router.post("/purchase")
@explicit_cross_portal_tenant(
    reason=(
        "Purchase endpoint looks up a global pack SKU. "
        "Becomes @scope_to_portal_tenant in C25."
    )
)
def purchase(
    body: PurchaseRequest,
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    pack = session.get(TokenPackORM, body.pack_id)
    if pack is None or not pack.is_active:
        raise NotFoundError("Token pack not found.", code="pack_not_found")
    return JSONResponse(
        status_code=501,
        content={
            "code": "payment_not_implemented",
            "message": "Online payment coming soon. Contact support@sran.ai to purchase tokens.",
            "pack": {
                "id": pack.id,
                "name": pack.name,
                "tokens": int(pack.tokens),
                "price_usd": float(pack.price_usd),
            },
        },
    )
