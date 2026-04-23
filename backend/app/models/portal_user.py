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
    # Phase 3 — portal tenancy. Every portal user belongs to exactly
    # one tenant in production; migration 030 enforces this via a DB
    # NOT NULL constraint. The ORM leaves it nullable so pre-Phase-3
    # test fixtures (which bypass migrations via metadata.create_all)
    # can still seed users — application code in auth_service.signup
    # is the gatekeeper that guarantees tenant assignment in practice.
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
    # Phase 3.5 — transactional email language preference. 'en' |
    # 'th' today, string (not enum) so adding more locales post-
    # launch needs zero DDL. Unknown values fall back to English at
    # render time.
    preferred_language: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="en"
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
