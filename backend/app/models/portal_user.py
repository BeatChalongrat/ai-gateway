from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class PortalUserORM(Base):
    __tablename__ = "portal_users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    credit_balance: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    credit_used: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    plan: Mapped[str] = mapped_column(String(32), nullable=False, server_default="free_trial")
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="active")
    email_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    # OAuth linkage (set when the account is created or linked via
    # Google/GitHub "continue with..."; NULL for password-only users).
    oauth_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    oauth_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Phase 3 — portal tenancy (migration 029). Starts nullable; flipped
    # to NOT NULL by migration 030 once the backfill has populated every
    # row. Application code treats tenant_id as mandatory from day one.
    tenant_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("tenants.tenant_id", name="fk_portal_users_tenant_id"),
        nullable=True,
    )
    tenant_role: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="member"
    )
    # Bumped at signup migration + on every credential-reset event so
    # stale JWTs stop validating the moment ownership / tenancy changes.
    token_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
