"""ORM models for the AI Sensitive Data Reviewer feature.

- ``AiReviewSettingsORM`` — singleton row with feature flag + scope +
  dedicated provider/key config + realtime sampling toggle.
- ``AiReviewFindingORM`` — append-only log of individual findings
  produced by a scan run. Grouped by ``scan_run_id``.
- ``AiReviewScanRunORM`` — one row per scan execution (manual or
  realtime) carrying token / cost aggregates so the admin can audit
  system cost over time.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AiReviewSettingsORM(Base):
    __tablename__ = "ai_review_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa.text("false")
    )
    # JSON-encoded list[str] of key_ids. None/NULL means "all keys".
    monitored_key_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    scan_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa.text("50")
    )
    last_scan_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=True
    )

    # --- Provider config (migration 023) ---
    # All NULL-able: if ``review_provider`` is NULL the scan endpoint
    # falls back to Anthropic + ANTHROPIC_API_KEY from env (original
    # behavior, unchanged for existing installs).
    review_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    review_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Encoded via base64 (same scheme as ProviderKeyService._encode).
    # Never returned to clients — only a ``review_api_key_set`` bool.
    review_api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_endpoint: Mapped[str | None] = mapped_column(Text, nullable=True)

    realtime_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa.text("false")
    )
    realtime_sample_rate: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa.text("10")
    )


class AiReviewFindingORM(Base):
    __tablename__ = "ai_review_findings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    scan_run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    sample_text: Mapped[str] = mapped_column(Text, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    is_in_coverage: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa.text("false")
    )
    suggested_detector: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=sa.text("'new'")
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=True
    )


class AiReviewScanRunORM(Base):
    __tablename__ = "ai_review_scan_runs"

    scan_run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    trigger: Mapped[str] = mapped_column(String(20), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    requests_scanned: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa.text("0")
    )
    findings_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa.text("0")
    )
    prompt_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa.text("0")
    )
    completion_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa.text("0")
    )
    total_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa.text("0")
    )
    estimated_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 8), nullable=False, server_default=sa.text("0")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=sa.text("'completed'")
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
