"""ORM model for ``generation_jobs`` — async media-generation job store.

Sprint 1 plumbing for video / music / TTS generation. A row is created
when a generation request is accepted, advanced by the lifespan job
loop (``app/services/media_gen/job_loop.py``), and reaches a terminal
``done`` / ``failed`` / ``cancelled`` status. See migration 056 for the
column rationale (String(36) ids, cross-database chat-edge references
without FK, JSON→JSONB variant, partial indexes).
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# JSONB on Postgres, plain JSON under the SQLite test engine.
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


class GenerationJobORM(Base):
    __tablename__ = "generation_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("portal_users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # chat-edge DB references — no FK (those tables live in a separate DB).
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # video | audio_tts | music | image_edit
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_job_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # pending_confirm | queued | processing | done | failed | cancelled
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'queued'")
    )
    progress_pct: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    input_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    input_metadata: Mapped[dict] = mapped_column(
        _JSON, nullable=False, server_default=text("'{}'")
    )

    output_file_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    output_metadata: Mapped[dict | None] = mapped_column(_JSON, nullable=True)

    estimated_cost_credits: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_cost_credits: Mapped[int | None] = mapped_column(Integer, nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ttl_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        Index(
            "ix_generation_jobs_status_created",
            "status",
            "created_at",
            postgresql_where=text("status IN ('queued','processing')"),
        ),
        Index(
            "uq_generation_jobs_idem",
            "tenant_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )
