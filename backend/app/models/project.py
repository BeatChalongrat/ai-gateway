from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import ForeignKey, Integer, String, Float, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base


class ProjectORM(Base):
    __tablename__ = "projects"

    project_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.tenant_id"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    policy_profile_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    budget_limit: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_requests_per_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )
    portal_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("portal_users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Soft-delete for the admin "Archive project" flow. Non-null means
    # archived — the admin UI hides these by default but surfaces them
    # under a "Show archived" toggle. 30-day window documented in the
    # admin UI; actual retention is enforced by a manual purge script,
    # not a cron (nothing about archive is security-critical so we
    # don't need an auto-hard-delete yet).
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Routing config (migration 044). All nullable — None means "defer
    # to global defaults / caller-supplied metadata". Injected into
    # ``payload.metadata`` by the orchestrator BEFORE the router chain
    # runs, where caller-supplied keys still take precedence. See
    # ``services/orchestrator._inject_project_routing_config``.
    default_model: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    preferred_provider: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    # Portable JSON columns: ``JSONB`` on Postgres (native binary,
    # supports ``->`` operators) and generic ``JSON`` on SQLite so the
    # test suite's in-memory engine can still create the table. Same
    # ``with_variant`` pattern used by ``request_log.fallback_attempts``.
    allowed_providers: Mapped[list[str] | None] = mapped_column(
        sa.JSON().with_variant(JSONB(), "postgresql"),
        nullable=True,
    )
    blocked_models: Mapped[list[str] | None] = mapped_column(
        sa.JSON().with_variant(JSONB(), "postgresql"),
        nullable=True,
    )
