from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import Boolean, String, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class TenantORM(Base):
    __tablename__ = "tenants"

    tenant_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="active"
    )
    region: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="global"
    )
    ner_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa.text("false")
    )
    # Phase-3c per-tenant DLP engine override. ``inherit`` defers to
    # the global ``DLP_ENGINE`` setting; ``python`` / ``rust`` /
    # ``shadow`` pin this tenant regardless of the global. Migration
    # 043 backfills every existing row with ``inherit``.
    dlp_engine: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="inherit"
    )
    # Credit billing multiplier applied to this tenant's request cost
    # before converting to ledger credits (migration 054). 1.0 = at
    # cost, 1.5 = 50% margin, 2.0 = 100% margin. Used identically by
    # the balance deduction AND the usage-panel display so they match.
    credit_markup: Mapped[float] = mapped_column(
        sa.Numeric(4, 2), nullable=False, server_default=sa.text("1.0")
    )
    # Web-search feature (migration 055). ``search_enabled`` gates the
    # whole feature for the tenant — chat-edge only offers the
    # web_search tool when this is on AND a provider is configured.
    # ``search_credit_cost`` is the flat credit charge per search query
    # (web search bills per-query, not by token cost).
    search_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa.text("false")
    )
    search_credit_cost: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("5")
    )
    # Media generation (migration 056). ``dlp_media_gen_mask_identity``:
    # per-tenant override — when True, mask identity-class PII in media-gen
    # prompts (enterprise-strict); default False = industry-standard
    # pass-through. ``monthly_*_quota`` NULL = unlimited.
    dlp_media_gen_mask_identity: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa.text("false")
    )
    # Phase 9 (migration 064) — per-tenant raw-egress policy for the two
    # INTENTIONAL unmasked channels. "allow" (default) = identity PII may
    # egress raw (audited); "block" = fail closed on ANY identity PII in
    # a search query / media prompt. The credential/secret class blocks
    # regardless. (Supersedes the never-wired dlp_media_gen_mask_identity
    # above — masking a media prompt was rejected in favor of block-or-
    # allow; the dormant column stays for migration compatibility.)
    search_pii_egress: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="allow"
    )
    media_pii_egress: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="allow"
    )
    # FIX 2 (migration 065) — per-tenant raw-egress policy for the two
    # ADDITIONAL intentional unmasked channels the gateway now fronts so
    # chat-edge stops calling OpenAI directly: ``kb_pii_egress`` governs
    # embeddings (KB document indexing + RAG query text), ``voice_pii_egress``
    # governs voice (TTS input text + STT audio). "allow" (default) =
    # identity PII may egress raw (audited); "block" = fail closed on ANY
    # identity PII (and, for STT, refuse the un-scannable audio outright).
    # The credential/secret class blocks regardless.
    kb_pii_egress: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="allow"
    )
    voice_pii_egress: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="allow"
    )
    monthly_video_quota: Mapped[int | None] = mapped_column(
        sa.Integer, nullable=True
    )
    monthly_audio_quota: Mapped[int | None] = mapped_column(
        sa.Integer, nullable=True
    )
    # L2 spend guard (migration 058). Hard per-tenant daily USD ceiling on
    # real media generation. Default $10; raise per-tenant via DB (audited).
    max_daily_spend_usd: Mapped[Decimal] = mapped_column(
        sa.Numeric(10, 2), nullable=False, server_default=sa.text("10.00")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )
