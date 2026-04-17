"""ORM model for organization-level gateway settings (singleton row)."""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class OrgSettingsORM(Base):
    """Single-row table for org-wide runtime toggles.

    Currently stores only ``ner_enabled``. Additional toggles can be added
    here without creating new tables. A fixed primary key of 1 enforces
    the singleton — the service layer always reads/writes id=1.
    """

    __tablename__ = "org_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    ner_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa.text("false")
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )
