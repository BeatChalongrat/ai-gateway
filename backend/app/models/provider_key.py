"""Provider API key storage — supports multiple keys per provider."""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import String, Boolean, Integer, DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ProviderKeyORM(Base):
    __tablename__ = "provider_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provider_name: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=sa.text("true"))
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # valid, invalid, untested
    last_test_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        sa.UniqueConstraint("provider_name", "label", name="uq_provider_key_label"),
    )
