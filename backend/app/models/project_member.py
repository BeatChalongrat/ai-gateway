"""Explicit project membership — one row per (project, user).

Added 2026-04-24 as part of the admin project-management UI. Pre-034
the schema carried an implicit "creator" via projects.portal_user_id
and treated every tenant user as having implicit access; this table
makes that explicit so owners can attach / detach members without
touching project ownership.

Roles today: 'admin' | 'member' | 'viewer'. Enforcement still happens
at the route layer — this ORM just stores the fact.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ProjectMemberORM(Base):
    __tablename__ = "project_members"

    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "projects.project_id",
            name="fk_project_members_project_id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "portal_users.id",
            name="fk_project_members_user_id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="member"
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Not a FK — if the adder gets hard-deleted we still want the audit
    # trail of "someone added this person," and we store the display
    # name separately in logs. SET NULL-on-delete semantics would be
    # fine too; we just avoid the cascade dance by keeping it opaque.
    added_by: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )
