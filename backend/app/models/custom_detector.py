"""ORM model for ``custom_detectors`` — admin-defined DLP detectors (Sprint 6).

Detector-as-Data: a row defines a regex / keyword-list / semantic detector
that runs alongside the 31 built-in detectors. The loader
(``custom_detector_loader.py``) caches the ``active``/``testing`` rows and the
registry runs them on the scan hot path. See migration 061.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


class CustomDetectorORM(Base):
    __tablename__ = "custom_detectors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # pii | identity | financial | credential | secret | network | content_policy
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    # regex | keyword_list | semantic
    detection_type: Mapped[str] = mapped_column(String(16), nullable=False)
    pattern: Mapped[dict] = mapped_column(_JSON, nullable=False)
    severity: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'medium'")
    )
    default_action: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'mask'")
    )
    # draft | testing | active | disabled
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'draft'")
    )
    test_cases: Mapped[list] = mapped_column(
        _JSON, nullable=False, server_default=text("'[]'")
    )
    test_passed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    last_match_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    last_matched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    timeout_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    created_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("portal_users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )

    __table_args__ = (
        Index(
            "ix_custom_detectors_active",
            "status",
            postgresql_where=text("status IN ('active', 'testing')"),
        ),
        CheckConstraint(
            "status IN ('draft', 'testing', 'active', 'disabled')",
            name="ck_custom_detectors_status",
        ),
        CheckConstraint(
            "detection_type IN ('regex', 'keyword_list', 'semantic')",
            name="ck_custom_detectors_detection_type",
        ),
        CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_custom_detectors_severity",
        ),
        CheckConstraint(
            "default_action IN ('mask', 'block', 'pass', 'restore')",
            name="ck_custom_detectors_default_action",
        ),
    )

    def to_dict(self) -> dict:
        """Serialize the fields the loader / executor need on the hot path.

        Deliberately plain primitives so a cached snapshot is decoupled from
        the ORM session (the loader holds these across requests).
        """
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "detection_type": self.detection_type,
            "pattern": self.pattern,
            "severity": self.severity,
            "default_action": self.default_action,
            "status": self.status,
            "version": self.version,
        }
