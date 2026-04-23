from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class TenantInviteORM(Base):
    """Owner-generated invite for joining a tenant.

    Post-2026-04-24 (invite-flow redesign): codes are multi-use
    shareable links, not single-use tokens. A row is "live" iff
    ``revoked_at IS NULL AND expires_at > now()``. ``used_at`` /
    ``used_by`` track the last successful redemption for audit, but
    re-redeeming a live code is allowed — that's how the "one link,
    share it with the whole team" UX works.

    Constraints (enforced by migration 033):
      * ``code`` UNIQUE across all tenants (bearer-token contract).
      * One un-revoked row per tenant (partial unique index on
        ``tenant_id WHERE revoked_at IS NULL``). Regenerate must
        explicitly revoke before creating a replacement.
    """

    __tablename__ = "tenant_invites"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "tenants.tenant_id",
            name="fk_tenant_invites_tenant_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # The role the invitee is granted on redemption. Today only
    # ``'member'`` is exercised; the Team UI keeps ``'admin'`` as a
    # disabled option until the full RBAC tier lands (design doc §14).
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="member"
    )
    created_by: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "portal_users.id",
            name="fk_tenant_invites_created_by",
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Last redemption timestamp (audit-only — invite stays live).
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    used_by: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(
            "portal_users.id",
            name="fk_tenant_invites_used_by",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    # Soft-delete for the Revoke flow. Set once; a revoked row never
    # flips back to live. Pre-redesign we overloaded ``used_at`` for
    # revocation which made "last redemption" ambiguous.
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    email_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
