"""ORM model for ``dlp_rule_audit_log`` — change history for custom detectors.

Every CRUD / lifecycle transition on a custom detector writes one row here
(from ``CustomDetectorService``, never from a route directly). The FK to
``custom_detectors`` is ``ON DELETE SET NULL`` so a hard-deleted detector
keeps its trail with a snapshot of its name. See migration 061.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


class DlpRuleAuditLogORM(Base):
    __tablename__ = "dlp_rule_audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    custom_detector_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("custom_detectors.id", ondelete="SET NULL"),
        nullable=True,
    )
    detector_name: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    # Admin actor — plain audit field, no FK (decoupled from any user store).
    changed_by_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    before_value: Mapped[dict | None] = mapped_column(_JSON, nullable=True)
    after_value: Mapped[dict | None] = mapped_column(_JSON, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_audit_log_detector", "custom_detector_id", "changed_at"),
        Index("ix_audit_log_user", "changed_by_user_id", "changed_at"),
    )
