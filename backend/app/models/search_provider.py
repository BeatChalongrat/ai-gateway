"""Search provider configuration — per-tenant, priority-ordered.

Mirrors provider_key.py for the web-search feature: an AES-256-GCM
key at rest (Text, decoded via app.core.security like provider keys),
a fallback-chain priority, health tracking, and usage counters.
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class SearchProviderORM(Base):
    __tablename__ = "search_providers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)

    # 'tavily' | 'brave' | 'serpapi' | 'google' | 'bing'
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # AES-256-GCM at rest (same encode/decode as provider keys).
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

    # Lower = higher priority (0 = primary).
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa.text("0")
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa.text("true")
    )
    max_results: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa.text("5")
    )

    # 'healthy' | 'degraded' | 'down' | 'unknown'
    health_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="unknown"
    )
    last_health_check: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_failure_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    uptime_pct: Mapped[float | None] = mapped_column(
        Numeric(5, 2), nullable=True, server_default=sa.text("100.0")
    )
    avg_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    total_queries: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa.text("0")
    )
    total_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa.text("0")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id", "provider", "priority", name="uq_search_provider_priority"
        ),
    )
