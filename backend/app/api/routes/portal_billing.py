"""Portal billing: token packs + (stubbed) purchase.

Stripe isn't wired in yet. ``POST /portal/billing/purchase`` returns
HTTP 501 with a pointer to support@sran.ai — the response carries the
selected pack so the UI can show "You tried to buy Pack X".
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_db_session, get_portal_user
from app.api.portal_scope import scope_to_portal_tenant
from app.core.exceptions import NotFoundError
from app.models.portal_user import PortalUserORM
from app.models.token_pack import TokenPackORM


router = APIRouter(dependencies=[Depends(get_portal_user)])


class PurchaseRequest(BaseModel):
    pack_id: str


def _visible_packs_filter(user: PortalUserORM):
    """Packs visible to a caller: global (tenant_id IS NULL) + the
    caller's tenant-private packs. Using or_ keeps the query readable
    vs a string-built WHERE."""
    return or_(
        TokenPackORM.tenant_id.is_(None),
        TokenPackORM.tenant_id == user.tenant_id,
    )


@router.get("/packs")
@scope_to_portal_tenant
def list_packs(
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    """List active token packs visible to the caller.

    Phase 3 C25: packs are tenant-scoped. A pack with
    ``tenant_id IS NULL`` is a global SKU; any non-null value makes
    it private to that tenant. The caller sees global + their own
    tenant's packs, never another tenant's.
    """
    rows = (
        session.execute(
            select(TokenPackORM)
            .where(TokenPackORM.is_active == True)  # noqa: E712
            .where(_visible_packs_filter(user))
            .order_by(
                TokenPackORM.sort_order.asc(), TokenPackORM.created_at.asc()
            )
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
            "tenant_id": p.tenant_id,
        }
        for p in rows
    ]


@router.post("/purchase")
@scope_to_portal_tenant
def purchase(
    body: PurchaseRequest,
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    """Simulate a purchase.

    Gating: the pack must be active AND either global or belong to
    the caller's tenant. Requesting a pack scoped to another tenant
    surfaces as ``pack_not_found`` — the same response as a truly
    missing id, no existence oracle.
    """
    pack = session.get(TokenPackORM, body.pack_id)
    if pack is None or not pack.is_active:
        raise NotFoundError("Token pack not found.", code="pack_not_found")
    if pack.tenant_id is not None and pack.tenant_id != user.tenant_id:
        # Deliberately identical to the "not found" response so a
        # caller can't probe which pack ids exist in other tenants.
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
