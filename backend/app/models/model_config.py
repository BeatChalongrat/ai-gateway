"""ORM model for the DB-backed model registry.

Mirrors ``ModelCandidate`` (``app/domain/routing.py``) for the routing
hot-path fields, plus discovery metadata that lets the periodic sync
job upsert without overwriting admin edits.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, Float, Integer, String, func
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ModelConfigORM(Base):
    """A single model registry row.

    ``(provider, model)`` is unique. The registry singleton in
    ``app/services/providers/model_registry.py`` loads only
    ``is_active=True`` rows; the discovery sync flips
    ``is_active=False`` for models that disappear upstream rather
    than hard-deleting them, so historical request_logs that
    reference the row still resolve.
    """

    __tablename__ = "model_configs"
    __table_args__ = (
        sa.UniqueConstraint(
            "provider", "model",
            name="uq_model_configs_provider_model",
        ),
        sa.Index("ix_model_configs_provider", "provider"),
        sa.Index("ix_model_configs_is_active", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)

    # --- Capability ---
    supports_streaming: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa.text("true")
    )
    supports_tools: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa.text("false")
    )
    supports_vision: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa.text("false")
    )
    # Reasoning / thinking capability (migration 045). Mirrors the
    # ``supports_streaming/tools/vision`` pattern. ``reasoning_strategy``
    # names the adapter pathway used to translate the public
    # ``ReasoningConfig`` into provider-native shape (T2 consumes it).
    supports_reasoning: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa.text("false")
    )
    reasoning_strategy: Mapped[str] = mapped_column(
        String(80), nullable=False, server_default=sa.text("'none'")
    )
    default_reasoning_mode: Mapped[str | None] = mapped_column(
        String(20), nullable=True, server_default=sa.text("'auto'")
    )
    max_context_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa.text("128000")
    )

    # --- Cost / latency ---
    cost_tier: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=sa.text("'standard'")
    )
    latency_tier: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=sa.text("'standard'")
    )
    cost_per_input_token: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=sa.text("0.0")
    )
    cost_per_output_token: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=sa.text("0.0")
    )

    # --- Media generation (migration 056) ---
    # Capability flags drive the /v1/models wire capabilities
    # (supports_video_gen -> "video_generation", etc.). Cost fields are
    # per-unit USD: video bills per second, TTS per character, music per
    # generation. NULL where not applicable to the model's modality.
    supports_video_gen: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa.text("false")
    )
    supports_audio_gen: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa.text("false")
    )
    supports_tts: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa.text("false")
    )
    # FIX67 — image generation is a catalogue capability like video/tts
    # (migration 085), so a second image backend is a row + adapter, not a
    # hardcoded frozenset. Image rows stay is_active=false so they never enter
    # the chat routing pool (0 per-token cost would otherwise win auto).
    supports_image_gen: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa.text("false")
    )
    cost_per_second: Mapped[Decimal | None] = mapped_column(
        sa.Numeric(10, 6), nullable=True
    )
    cost_per_character: Mapped[Decimal | None] = mapped_column(
        sa.Numeric(12, 8), nullable=True
    )
    cost_per_generation: Mapped[Decimal | None] = mapped_column(
        sa.Numeric(10, 4), nullable=True
    )

    # --- Discovery metadata ---
    # source ∈ {"auto", "manual", "seed"}.
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=sa.text("'auto'")
    )
    # is_manual_override=True means discovery sync skips this row.
    is_manual_override: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa.text("false")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa.text("true")
    )
    # FIX47 Part C — the second axis (migration 080). ``is_active`` says
    # the API may call the model; ``show_in_chat`` says the chat picker
    # offers it. An admin curates this from the admin UI; there is no
    # list in code.
    show_in_chat: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa.text("true")
    )
    # FIX64 — where each capability flag came from (migration 083):
    # ``{"tools": {"source": "admin"|"provider"|"inferred"|"probe"|"seed",
    # "at": iso, "evidence": str}, ..., "tests": {cap: last test result}}``.
    # ``{}`` for rows that predate the column: unknown, not guessed.
    capability_provenance: Mapped[dict] = mapped_column(
        sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
        nullable=False,
        server_default=sa.text("'{}'"),
        default=dict,
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
