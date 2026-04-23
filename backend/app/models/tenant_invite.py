from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class TenantInviteORM(Base):
    """Owner-generated invite for joining a tenant.

    Invite codes are bearer tokens: anyone with the code + a signup
    request can redeem it, up to ``used_at``. 7-day default expiry,
    single-use. Email is a display hint only, not enforced at redeem
    time. See ``PORTAL_TENANCY_MIGRATION_PLAN.md`` §8 for the
    internal-tool rationale (copy-paste codes, no mailer).
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
    email_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
