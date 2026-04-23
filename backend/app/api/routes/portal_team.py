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
from app.api.portal_scope import scope_to_portal_tenant
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
    # avatar_url is included so the Team page can render the same
    # circle the user sees in the sidebar. Nullable — falls back to the
    # deterministic initial+colour in the Avatar component.
    return {
        "id": u.id,
        "email": u.email,
        "display_name": u.display_name,
        "avatar_url": u.avatar_url,
        "tenant_role": u.tenant_role,
        "status": u.status,
        "created_at": u.created_at,
    }


def _invite_to_dict(inv: TenantInviteORM, *, include_code: bool = False) -> dict:
    body = {
        "id": inv.id,
        "tenant_id": inv.tenant_id,
        "role": inv.role,
        "email_hint": inv.email_hint,
        "created_at": inv.created_at,
        "expires_at": inv.expires_at,
        "used_at": inv.used_at,
        "used_by": inv.used_by,
        "revoked_at": inv.revoked_at,
    }
    if include_code:
        body["code"] = inv.code
        # Frontend composes and displays this, but including it in the
        # response means the "Copy link" button has a single source of
        # truth for the URL shape — otherwise a host-config mismatch
        # between frontend and backend produces confusing "404 on
        # valid code" reports.
        from app.core.config import settings

        body["invite_url"] = (
            f"{settings.portal_public_url.rstrip('/')}/invite/{inv.code}"
        )
    return body


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


@router.get("/members")
@scope_to_portal_tenant
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
@scope_to_portal_tenant
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
@scope_to_portal_tenant
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
@scope_to_portal_tenant
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
@scope_to_portal_tenant
def revoke_invite(
    invite_id: str,
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    """Revoke a pending invite by marking it revoked.

    Owner-only. Revoked invites are still in the DB (audit trail)
    but cannot be redeemed. We do not delete the row so an operator
    can reconstruct who revoked what and when.

    Post-redesign (2026-04-24): sets ``revoked_at`` instead of the
    old ``used_at``-sentinel overload. Redemption checks both fields;
    the column split lets "2 people used this link, nobody revoked
    it" stay distinct from "1 owner killed the link."
    """
    tenant_id = _require_owner(user)
    invite = session.get(TenantInviteORM, invite_id)
    if invite is None or invite.tenant_id != tenant_id:
        raise NotFoundError(
            "Invite not found.", code="invite_not_found"
        )
    if invite.revoked_at is not None:
        # Already revoked — idempotent ok.
        return {"message": "ok"}
    invite.revoked_at = datetime.now(timezone.utc)
    session.flush()
    return {"message": "ok"}


# ---------------------------------------------------------------------------
# Redemption (authenticated user joining a tenant)
# ---------------------------------------------------------------------------


class RedeemRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=128)


@router.post("/redeem")
@scope_to_portal_tenant
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
        "tenant_role": invite.role or "member",
    }


# ---------------------------------------------------------------------------
# Invite-flow redesign (2026-04-24) — singular /invite endpoints.
#
# The plural /invites endpoints above stay for backward compatibility
# (they served the pre-redesign "copy the code" UI). The new UI talks
# to the singular routes which enforce "one live invite per tenant"
# at the handler level. Both sets share the same tenant_service
# primitives, so there's no risk of them drifting.
# ---------------------------------------------------------------------------


class CreateInviteLinkRequest(BaseModel):
    role: str = Field(default="member", max_length=16)
    force: bool = Field(default=False)


@router.get("/invite")
@scope_to_portal_tenant
def get_active_invite(
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    """Return the currently-live invite for the caller's tenant.

    Returns 200 with a null body when no live invite exists, rather
    than 404. The UI decides between "Create invite link" (null) and
    the live-link card (non-null) and a 200+null lets it skip the
    error-handling code path entirely. Owner-only.
    """
    tenant_id = _require_owner(user)
    invite = tenant_service.get_live_invite(session, tenant_id=tenant_id)
    if invite is None:
        return {"invite": None}
    return {"invite": _invite_to_dict(invite, include_code=True)}


@router.post("/invite")
@scope_to_portal_tenant
def create_or_regenerate_invite(
    body: CreateInviteLinkRequest,
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    """Create a shareable invite link.

    Two modes:
      * ``force=false`` (default): if a live invite already exists,
        return it idempotently. First call on a fresh tenant creates
        a new one.
      * ``force=true``: revoke the existing live invite (if any) and
        create a new one. This is how the "Regenerate link" button
        works — the old URL stops working immediately so a leaked
        link can be killed in one click.
    """
    tenant_id = _require_owner(user)
    existing = tenant_service.get_live_invite(session, tenant_id=tenant_id)
    if existing is not None and not body.force:
        return {"invite": _invite_to_dict(existing, include_code=True)}

    if existing is not None:
        existing.revoked_at = datetime.now(timezone.utc)
        session.flush()

    invite = tenant_service.generate_invite(
        session,
        tenant_id=tenant_id,
        creator_user_id=user.id,
        role=body.role or "member",
    )
    return {"invite": _invite_to_dict(invite, include_code=True)}


@router.delete("/invite")
@scope_to_portal_tenant
def revoke_active_invite(
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    """Revoke the tenant's currently-live invite (if any).

    Idempotent: if nothing is live, returns ok. Owner-only.
    """
    tenant_id = _require_owner(user)
    invite = tenant_service.get_live_invite(session, tenant_id=tenant_id)
    if invite is None:
        return {"message": "ok"}
    invite.revoked_at = datetime.now(timezone.utc)
    session.flush()
    return {"message": "ok"}


# ---------------------------------------------------------------------------
# Email-sender — Pattern-2 Block 2.
# ---------------------------------------------------------------------------


class SendInviteEmailRequest(BaseModel):
    recipients: list[str] = Field(default_factory=list, max_length=25)


class PublicInvitePreviewResponse(BaseModel):
    valid: bool
    tenant_name: str | None = None
    tenant_id: str | None = None
    role: str | None = None
    inviter_name: str | None = None
    expires_at: str | None = None


@router.post("/invite/send-email")
@scope_to_portal_tenant
def send_invite_emails(
    body: SendInviteEmailRequest,
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    """Fan-out the live invite link to a list of recipients via Resend.

    Does NOT mint per-recipient codes — every email gets the single
    tenant link, matching the "shareable link" model. If there's no
    live link, the call refuses (the UI also disables the button in
    that state, this is defence in depth).

    Returns per-recipient success/failure so the UI can show
    "2 of 3 sent" and keep the failed chip red.
    """
    from app.core.config import settings
    from app.models.tenant import TenantORM
    from app.services.email import send_invite_email

    tenant_id = _require_owner(user)
    invite = tenant_service.get_live_invite(session, tenant_id=tenant_id)
    if invite is None:
        raise NotFoundError(
            "No active invite link. Create one before sending emails.",
            code="no_active_invite",
        )

    tenant = session.get(TenantORM, tenant_id)
    tenant_name = tenant.name if tenant is not None else "your team"
    invite_url = (
        f"{settings.portal_public_url.rstrip('/')}/invite/{invite.code}"
    )

    # Basic input hygiene — dedupe (case-insensitive) and strip. The
    # per-recipient email validator lives in the schema for format
    # checks, but chip-style UIs sometimes submit rough strings, so
    # we normalise defensively before hitting the sender.
    seen: set[str] = set()
    clean: list[str] = []
    for r in body.recipients:
        addr = (r or "").strip().lower()
        if not addr or "@" not in addr:
            clean.append("")  # placeholder for index alignment
            continue
        if addr in seen:
            clean.append("")
            continue
        seen.add(addr)
        clean.append(addr)

    results: list[dict] = []
    for i, addr in enumerate(clean):
        original = body.recipients[i]
        if not addr:
            results.append({
                "email": original,
                "delivered": False,
                "reason": "invalid_or_duplicate",
            })
            continue
        r = send_invite_email(
            to=addr,
            inviter_name=user.display_name or user.email,
            tenant_name=tenant_name,
            invite_link=invite_url,
            invite_code=invite.code,
            language=user.preferred_language or "en",
        )
        results.append({
            "email": addr,
            "delivered": bool(r.delivered),
            "reason": r.reason,
        })

    sent = sum(1 for r in results if r["delivered"])
    return {
        "sent": sent,
        "total": len(results),
        "results": results,
    }
