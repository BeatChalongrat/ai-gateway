"""Portal tenancy service: create tenants, mint/redeem invites, manage members.

Phase 3 Commit 19. Implements the 5 primitives called out in
``PORTAL_TENANCY_MIGRATION_PLAN.md`` §5.1:

- ``create_tenant_with_owner``: atomic tenant + owner-user creation.
- ``generate_invite``: owner-only, returns a 43-char opaque code.
- ``redeem_invite``: validates, marks used, assigns redeeming user.
- ``list_members``: all users in a tenant.
- ``remove_member``: owner-only, refuses to remove the last owner.

Design principles:

* Every mutation is wrapped in an explicit commit/rollback by the
  caller (service is sync-ORM-native, same as the rest of the
  portal stack).
* Invite codes are bearer tokens — once leaked, they can be
  redeemed by anyone who has them. 7-day expiry + single-use is
  the ceiling on blast radius. See design doc §8 for the
  internal-tool rationale.
* Role hierarchy is intentionally flat: ``'owner' | 'member'``.
  Extension to richer RBAC is deferred (§14).
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.models.portal_user import PortalUserORM
from app.models.tenant import TenantORM
from app.models.tenant_invite import TenantInviteORM


# ``secrets.token_urlsafe(32)`` returns ~43 chars from a 32-byte seed;
# 256 bits of entropy is deep overkill for an internal-tool invite but
# costs nothing. 7 days is the design-doc default (§15 Q2).
_INVITE_CODE_BYTES = 32
_DEFAULT_INVITE_TTL = timedelta(days=7)


# ---------------------------------------------------------------------------
# Tenant creation
# ---------------------------------------------------------------------------


def create_tenant_with_owner(
    db: Session,
    *,
    tenant_name: str,
    owner_user_id: str,
) -> TenantORM:
    """Create a new tenant and mark the given user as its owner.

    Caller is responsible for ``db.commit()`` — this function flushes
    but does not commit, mirroring the pattern in
    ``auth_service.signup``.
    """
    name = (tenant_name or "").strip()
    if not name:
        raise ValidationError(
            "Tenant name must not be empty.", code="invalid_tenant_name"
        )
    if len(name) > 255:
        raise ValidationError(
            "Tenant name must be at most 255 characters.",
            code="invalid_tenant_name",
        )

    user = db.get(PortalUserORM, owner_user_id)
    if user is None:
        raise NotFoundError(
            "Owner user not found.", code="owner_user_not_found"
        )

    tenant = TenantORM(
        tenant_id=str(uuid.uuid4()),
        name=name,
        slug=_slugify_for_tenant(name, db),
        status="active",
        region="global",
        ner_enabled=False,
    )
    db.add(tenant)
    db.flush()

    user.tenant_id = tenant.tenant_id
    user.tenant_role = "owner"
    user.token_version = (user.token_version or 1) + 1
    db.flush()
    return tenant


def _slugify_for_tenant(name: str, db: Session) -> str:
    """Produce a unique slug from the tenant name.

    Tenant.slug is UNIQUE, so we suffix with a short random token when
    a collision happens. Internal tool scale: we only expect a handful
    of tenants, so O(N) retries are fine.
    """
    base = "".join(
        c if c.isalnum() else "-" for c in name.lower()
    ).strip("-") or "team"
    base = base[:48]  # Room for a suffix without blowing past 64 chars.
    candidate = base
    for _ in range(5):
        existing = db.execute(
            select(TenantORM.tenant_id).where(TenantORM.slug == candidate)
        ).scalar_one_or_none()
        if existing is None:
            return candidate
        candidate = f"{base}-{secrets.token_hex(4)}"
    # Extremely unlikely — 5 consecutive collisions on 32-bit suffix.
    raise ConflictError(
        "Could not allocate a unique tenant slug.",
        code="slug_allocation_failed",
    )


# ---------------------------------------------------------------------------
# Invites
# ---------------------------------------------------------------------------


def generate_invite(
    db: Session,
    *,
    tenant_id: str,
    creator_user_id: str,
    email_hint: str | None = None,
    ttl: timedelta | None = None,
    role: str = "member",
) -> TenantInviteORM:
    """Generate an invite code for the given tenant.

    Post-redesign (2026-04-24): invites are multi-use-until-expiry.
    Callers should check for an existing live invite first (see
    ``get_live_invite``) — this function always creates a new row
    and will collide with the partial unique index if one already
    exists un-revoked. The handler layer auto-revokes the old row
    when a force-regenerate is requested.

    Creator must be an owner of the tenant. ``email_hint`` is a
    display-only string carried for audit (e.g. "generated while
    inviting bob@x.com by email"); redemption does not check it.
    """
    creator = db.get(PortalUserORM, creator_user_id)
    if creator is None:
        raise NotFoundError(
            "Creator user not found.", code="creator_not_found"
        )
    if creator.tenant_id != tenant_id or creator.tenant_role != "owner":
        raise AuthorizationError(
            "Only tenant owners may generate invites.",
            code="invite_requires_owner",
        )

    if role not in ("member", "admin"):
        raise ValidationError(
            "Invite role must be 'member' or 'admin'.",
            code="invalid_invite_role",
        )

    now = datetime.now(timezone.utc)
    expires_at = now + (ttl or _DEFAULT_INVITE_TTL)

    invite = TenantInviteORM(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        code=secrets.token_urlsafe(_INVITE_CODE_BYTES),
        role=role,
        created_by=creator_user_id,
        expires_at=expires_at,
        email_hint=(email_hint or "").strip() or None,
    )
    db.add(invite)
    db.flush()
    return invite


def get_live_invite(
    db: Session, *, tenant_id: str
) -> TenantInviteORM | None:
    """Return the currently-live invite for a tenant, or None.

    "Live" means ``revoked_at IS NULL AND expires_at > now()``. Used by
    the GET /portal/team/invite handler and by the email-send endpoint
    (which refuses to send if there's no live link to send).

    The partial unique index on (tenant_id WHERE revoked_at IS NULL)
    guarantees there's at most one un-revoked row per tenant. We still
    have to filter out expired-but-not-revoked rows because the index
    predicate can't reference now() (not immutable).
    """
    now = datetime.now(timezone.utc)
    rows = (
        db.execute(
            select(TenantInviteORM)
            .where(TenantInviteORM.tenant_id == tenant_id)
            .where(TenantInviteORM.revoked_at.is_(None))
            .order_by(TenantInviteORM.created_at.desc())
        )
        .scalars()
        .all()
    )
    for r in rows:
        exp = r.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp > now:
            return r
    return None


def revoke_invite_by_code(
    db: Session, *, tenant_id: str, code: str
) -> TenantInviteORM:
    """Mark an invite as revoked. Owner-enforced at route layer.

    Tenant scope is a belt-and-braces check: the caller has already
    asserted ownership in the tenant, so mismatching tenant_id here
    means something is seriously wrong (forged URL, logic bug). We
    404 rather than 403 so we don't reveal whether the code exists in
    another tenant.
    """
    inv = db.execute(
        select(TenantInviteORM).where(TenantInviteORM.code == code)
    ).scalar_one_or_none()
    if inv is None or inv.tenant_id != tenant_id:
        raise NotFoundError(
            "Invite not found.", code="invite_not_found"
        )
    if inv.revoked_at is not None:
        return inv
    inv.revoked_at = datetime.now(timezone.utc)
    db.flush()
    return inv


def redeem_invite(
    db: Session,
    *,
    code: str,
    redeeming_user_id: str,
) -> TenantInviteORM:
    """Redeem an invite code and add the user to the invite's tenant.

    Post-redesign (2026-04-24): multi-use. A code is valid if it
    exists, is not revoked, and has not expired — re-redemption does
    not kill it (that's how shareable-link invites work). We still
    stamp ``used_at`` / ``used_by`` on every redemption so the last
    redeemer is recoverable for audit, but future redemptions simply
    overwrite those fields — they don't fail.

    The redeeming user inherits ``invite.role`` (today always
    'member'; the ``admin`` tier is wired end-to-end on the backend
    but hidden in the UI until RBAC work lands).
    """
    invite = db.execute(
        select(TenantInviteORM).where(TenantInviteORM.code == code)
    ).scalar_one_or_none()
    if invite is None:
        # Generic wording — no hint about whether the code exists or
        # is just expired. Information-leak guard per design doc §9.
        raise NotFoundError("Invite code is invalid.", code="invalid_invite")

    if invite.revoked_at is not None:
        raise ConflictError(
            "Invite link has been revoked.",
            code="invite_revoked",
        )
    now = datetime.now(timezone.utc)
    # SQLite stores DateTime timezone-naive; normalise before compare.
    expires_at = invite.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if now > expires_at:
        raise ConflictError(
            "Invite link has expired.", code="invite_expired"
        )

    user = db.get(PortalUserORM, redeeming_user_id)
    if user is None:
        raise NotFoundError(
            "Redeeming user not found.", code="redeeming_user_not_found"
        )
    if user.tenant_id and user.tenant_id != invite.tenant_id:
        raise ConflictError(
            "User already belongs to a different tenant.",
            code="user_has_tenant",
        )

    user.tenant_id = invite.tenant_id
    user.tenant_role = invite.role or "member"
    # Bump token_version so any sessions the redeemer had open before
    # redemption pick up the new tenant claim after one more login.
    user.token_version = (user.token_version or 1) + 1

    invite.used_at = now
    invite.used_by = redeeming_user_id
    db.flush()
    return invite


def preview_invite(
    db: Session, *, code: str
) -> TenantInviteORM | None:
    """Public read of an invite row for the invite-landing page.

    Returns the row only if live (not revoked, not expired). No auth
    — this is called from the pre-signup page so we can show
    "Joining {tenant_name} as {role}" context. Returns None for
    revoked/expired/missing so the caller decides how to present the
    error (same opaque message in every case to avoid leaking which
    codes exist).
    """
    inv = db.execute(
        select(TenantInviteORM).where(TenantInviteORM.code == code)
    ).scalar_one_or_none()
    if inv is None or inv.revoked_at is not None:
        return None
    exp = inv.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > exp:
        return None
    return inv


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


def list_members(db: Session, *, tenant_id: str) -> list[PortalUserORM]:
    """Return every portal user belonging to the given tenant."""
    return (
        db.execute(
            select(PortalUserORM)
            .where(PortalUserORM.tenant_id == tenant_id)
            .order_by(PortalUserORM.created_at.asc())
        )
        .scalars()
        .all()
    )


def remove_member(
    db: Session,
    *,
    tenant_id: str,
    actor_user_id: str,
    target_user_id: str,
) -> None:
    """Remove a member from a tenant. Owner-only.

    Refuses to remove the last owner so a tenant is never left
    ownerless. Self-removal is allowed for non-owner members; owners
    must transfer ownership first (deferred — see design doc §14).
    """
    actor = db.get(PortalUserORM, actor_user_id)
    if actor is None or actor.tenant_id != tenant_id:
        raise AuthorizationError(
            "Actor is not part of the tenant.",
            code="not_tenant_member",
        )
    if actor.tenant_role != "owner" and actor_user_id != target_user_id:
        raise AuthorizationError(
            "Only tenant owners may remove other members.",
            code="remove_requires_owner",
        )

    target = db.get(PortalUserORM, target_user_id)
    if target is None or target.tenant_id != tenant_id:
        raise NotFoundError(
            "Target user is not part of the tenant.",
            code="member_not_found",
        )

    if target.tenant_role == "owner":
        remaining_owners = db.execute(
            select(func.count(PortalUserORM.id)).where(
                PortalUserORM.tenant_id == tenant_id,
                PortalUserORM.tenant_role == "owner",
                PortalUserORM.id != target_user_id,
            )
        ).scalar_one()
        if remaining_owners < 1:
            raise ConflictError(
                "Cannot remove the last owner of a tenant. "
                "Promote another member first or delete the tenant.",
                code="last_owner_remaining",
            )

    # Orphan the user — clear tenant assignment and bump token_version
    # so their active sessions invalidate. Application-level signup is
    # the path by which they'd rejoin or get reassigned.
    target.tenant_id = None
    target.tenant_role = "member"
    target.token_version = (target.token_version or 1) + 1
    db.flush()
