"""Portal team — members + invite management.

Phase 3 Commit 22. Routes are owner-only for mutating operations
(enforce via the session's tenant_role claim / DB row). Members are
allowed to view team composition but not generate invites or remove
people. The redeem-invite path is mounted here too so an
already-signed-up user without a tenant can join one via a code.

All operations are scoped to the caller's own tenant — never a path
parameter — so forging a tenant_id in the URL is structurally
impossible. This matches the admin-side convention from Phase 2A.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db_session, get_portal_user
from app.core.exceptions import AuthorizationError, NotFoundError
from app.models.portal_user import PortalUserORM
from app.models.tenant_invite import TenantInviteORM
from app.services.portal import tenant_service


router = APIRouter(dependencies=[Depends(get_portal_user)])


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _require_tenant(user: PortalUserORM) -> str:
    """Portal team routes require the caller to belong to a tenant.

    Post-Phase-3 migration, portal_users.tenant_id is NOT NULL at the
    DB level (030), so only a drifted row would reach here without
    one. Fail closed with code=user_without_tenant.
    """
    if not user.tenant_id:
        raise AuthorizationError(
            "Your account is not attached to a tenant.",
            code="user_without_tenant",
        )
    return user.tenant_id


def _require_owner(user: PortalUserORM) -> str:
    """Gate for owner-only routes. Returns the tenant_id so the
    caller does not have to call _require_tenant separately."""
    tenant_id = _require_tenant(user)
    if user.tenant_role != "owner":
        raise AuthorizationError(
            "This action requires tenant-owner privileges.",
            code="owner_required",
        )
    return tenant_id


def _member_to_dict(u: PortalUserORM) -> dict:
    return {
        "id": u.id,
        "email": u.email,
        "display_name": u.display_name,
        "tenant_role": u.tenant_role,
        "status": u.status,
        "created_at": u.created_at,
    }


def _invite_to_dict(inv: TenantInviteORM, *, include_code: bool = False) -> dict:
    body = {
        "id": inv.id,
        "tenant_id": inv.tenant_id,
        "email_hint": inv.email_hint,
        "created_at": inv.created_at,
        "expires_at": inv.expires_at,
        "used_at": inv.used_at,
        "used_by": inv.used_by,
    }
    if include_code:
        body["code"] = inv.code
    return body


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


@router.get("/members")
def list_members(
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    """List all members of the caller's tenant.

    Visible to every member, not just owners — the team composition
    is not a secret within the tenant.
    """
    tenant_id = _require_tenant(user)
    members = tenant_service.list_members(session, tenant_id=tenant_id)
    return {"members": [_member_to_dict(m) for m in members]}


@router.delete("/members/{target_user_id}")
def remove_member(
    target_user_id: str,
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    """Remove a member from the caller's tenant.

    Owners may remove anyone (except the last owner). Non-owners may
    remove only themselves. Enforced inside ``tenant_service.remove_member``.
    """
    tenant_id = _require_tenant(user)
    tenant_service.remove_member(
        session,
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_user_id=target_user_id,
    )
    return {"message": "ok"}


# ---------------------------------------------------------------------------
# Invites
# ---------------------------------------------------------------------------


class InviteCreate(BaseModel):
    email_hint: str | None = Field(default=None, max_length=320)


@router.get("/invites")
def list_invites(
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    """List active (unredeemed, unexpired) invites for the tenant.

    Owner-only — the code values are included in the response so the
    UI can re-show them for copy-paste if the owner navigates away.
    """
    tenant_id = _require_owner(user)
    now = datetime.now(timezone.utc)
    rows = (
        session.execute(
            select(TenantInviteORM)
            .where(TenantInviteORM.tenant_id == tenant_id)
            .where(TenantInviteORM.used_at.is_(None))
            .order_by(TenantInviteORM.created_at.desc())
        )
        .scalars()
        .all()
    )
    active = []
    for inv in rows:
        expires = inv.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires > now:
            active.append(_invite_to_dict(inv, include_code=True))
    return {"invites": active}


@router.post("/invites")
def create_invite(
    body: InviteCreate,
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    """Generate a new invite code for the caller's tenant.

    Owner-only. Response includes the code exactly once — the owner
    copies it out of the UI and passes it to the invitee via
    whatever channel (Slack, email, in person).
    """
    tenant_id = _require_owner(user)
    invite = tenant_service.generate_invite(
        session,
        tenant_id=tenant_id,
        creator_user_id=user.id,
        email_hint=body.email_hint,
    )
    return _invite_to_dict(invite, include_code=True)


@router.delete("/invites/{invite_id}")
def revoke_invite(
    invite_id: str,
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    """Revoke a pending invite by marking it used.

    Owner-only. Revoked invites are still in the DB (audit trail)
    but cannot be redeemed. We do not delete the row so an operator
    can reconstruct who revoked what and when.
    """
    tenant_id = _require_owner(user)
    invite = session.get(TenantInviteORM, invite_id)
    if invite is None or invite.tenant_id != tenant_id:
        raise NotFoundError(
            "Invite not found.", code="invite_not_found"
        )
    if invite.used_at is not None:
        # Already redeemed or already revoked — idempotent ok.
        return {"message": "ok"}
    invite.used_at = datetime.now(timezone.utc)
    invite.used_by = None  # None indicates revocation, not redemption.
    session.flush()
    return {"message": "ok"}


# ---------------------------------------------------------------------------
# Redemption (authenticated user joining a tenant)
# ---------------------------------------------------------------------------


class RedeemRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=128)


@router.post("/redeem")
def redeem_invite(
    body: RedeemRequest,
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    """Redeem an invite code as an already-authenticated user.

    Intended for users whose current tenant is None (e.g., they were
    removed from a previous tenant) — joining a second tenant while
    still in one is rejected by the service layer.
    """
    invite = tenant_service.redeem_invite(
        session, code=body.code, redeeming_user_id=user.id
    )
    return {
        "tenant_id": invite.tenant_id,
        "tenant_role": "member",
    }
