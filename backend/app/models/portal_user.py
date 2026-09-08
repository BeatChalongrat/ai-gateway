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
    # Terminology (portal-wide convention):
    #   credits = prepaid balance (the thing the user buys / holds)
    #            — shown as "Available Credits", sidebar "N credits",
    #              Billing "Current Balance".
    #   tokens  = LLM metering unit (the raw provider counts, kept on
    #             request_logs for analytics) — shown as "Tokens Used",
    #             Usage page totals.
    # Deduction is COST-BASED, not token-lockstep: each billable
    # request debits ceil(cost_usd × tenant_markup ×
    # settings.usage_credits_per_usd), min 1 — see
    # ``credit_service.credits_for_cost`` +
    # ``portal_credit_check.deduct_portal_credit``. credit_used is the
    # lifetime sum of those debits (after clamp-at-zero), written in
    # the same transaction as the credit_transactions ledger row.
    credit_balance: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    credit_used: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    # FIX88 — the ceiling the balance is filled TO. Before this column
    # every screen derived "total" as balance + used, so a top-up
    # inflated the denominator and "full" could never be reached.
    # Migration 088 backfills it to exactly that derived value. Rules
    # (all in ``credit_service``): a manual top-up never lifts the
    # balance above the quota; signup bonus / purchase are GRANTS that
    # raise both; changing the quota moves the balance by the same
    # delta (floored at 0). Usage never touches it.
    credit_quota: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default="0",
        comment="Per-user credit ceiling; balance is filled to this, never above.",
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
    # one tenant; migration 030 enforces this via a DB NOT NULL
    # constraint and (SSO Phase 1, A1) the ORM now matches it so
    # metadata.create_all-based test databases enforce the same rule
    # instead of silently allowing tenant-less users that would crash
    # on a real migrated database. Creation paths that guarantee the
    # assignment: auth_service.signup (tenant resolved BEFORE the user
    # INSERT) and oauth_service.login_or_create (central tenant).
    tenant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenants.tenant_id", name="fk_portal_users_tenant_id"),
        nullable=False,
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
