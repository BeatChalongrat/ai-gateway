"""ORM model for persisted policy profiles (category action overrides)."""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class PolicyProfileORM(Base):
    """DB-backed overrides for built-in and custom policy profiles.

    The built-in DEFAULT_PROFILE / STRICT_PROFILE in
    ``services/policy/profiles.py`` remain the source of truth at import time.
    Rows in this table override those built-ins at runtime so super-admins
    can tune per-category enforcement actions without a code change.
    """

    __tablename__ = "policy_profiles"

    profile_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category_actions_json: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )
