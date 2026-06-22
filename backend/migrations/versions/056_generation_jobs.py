"""Add generation_jobs table + tenant/model_config media-gen columns.

Sprint 1 (multi-modal generation foundation). This is PLUMBING ONLY —
no user-visible feature ships against it yet. It backs the async
video/music/TTS generation worker that Sprint 2 wires up.

Schema notes (adapted to the real gateway schema, NOT the idealized
spec — same convention as migration 055):

- All ids are ``String(36)`` app-generated UUIDs, not native ``UUID``
  / ``gen_random_uuid()``. The gateway stores UUIDs as strings so the
  same migration + ORM run against the SQLite test engine (migration
  025 docstring) and Postgres alike.
- ``tenant_id`` references ``tenants.tenant_id`` (the String(36)
  business key like "demo-tenant-000"), matching every other FK in
  this schema. ``user_id`` references ``portal_users.id``.
- ``conversation_id`` / ``message_id`` / ``output_file_id`` are PLAIN
  nullable UUID columns with NO foreign key: the ``conversations`` and
  ``conversation_files`` tables live in the SEPARATE chat-edge database
  (a cross-database FK is impossible in Postgres). Integrity to those
  rows is enforced at the application layer.
- JSON columns use ``sa.JSON`` with a ``JSONB`` variant on Postgres so
  the column is JSONB in production but plain JSON under SQLite tests.
- The two indexes are PARTIAL on Postgres (``postgresql_where``); under
  SQLite the predicate is dropped and they degrade to full indexes,
  which is harmless for the test lane.

Revision ID: 056
Revises: 055
Create Date: 2026-06-22
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "056"
down_revision = "055"
branch_labels = None
depends_on = None


# JSON in production-Postgres becomes JSONB; under SQLite it stays JSON.
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "generation_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("portal_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # chat-edge DB references — no FK (cross-database).
        sa.Column("conversation_id", sa.String(36), nullable=True),
        sa.Column("message_id", sa.String(36), nullable=True),
        # video | audio_tts | music | image_edit
        sa.Column("job_type", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model_id", sa.String(255), nullable=False),
        sa.Column("external_job_id", sa.String(255), nullable=True),
        # queued | processing | done | failed | cancelled
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column("progress_pct", sa.SmallInteger(), nullable=True),
        sa.Column("input_prompt", sa.Text(), nullable=False),
        sa.Column(
            "input_metadata",
            _JSON,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        # chat-edge conversation_files reference — no FK (cross-database).
        sa.Column("output_file_id", sa.String(36), nullable=True),
        sa.Column("output_metadata", _JSON, nullable=True),
        sa.Column("estimated_cost_credits", sa.Integer(), nullable=False),
        sa.Column("actual_cost_credits", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ttl_expires_at", sa.DateTime(timezone=True), nullable=False),
    )

    # Worker scan index: only queued/processing rows matter to the poll
    # loop, so the index is partial on Postgres to stay small.
    op.create_index(
        "ix_generation_jobs_status_created",
        "generation_jobs",
        ["status", "created_at"],
        postgresql_where=sa.text("status IN ('queued','processing')"),
    )
    # Idempotency: at most one job per (tenant, key) when a key is given.
    # Partial on Postgres; on SQLite a full unique index over
    # (tenant_id, idempotency_key) — NULL keys stay distinct there too,
    # so unkeyed jobs are never blocked.
    op.create_index(
        "uq_generation_jobs_idem",
        "generation_jobs",
        ["tenant_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    # --- Tenant-level media-gen config (additive, no FK) ---
    op.add_column(
        "tenants",
        sa.Column(
            "dlp_media_gen_mask_identity",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "tenants",
        sa.Column("monthly_video_quota", sa.Integer(), nullable=True),
    )
    op.add_column(
        "tenants",
        sa.Column("monthly_audio_quota", sa.Integer(), nullable=True),
    )

    # --- model_configs media-gen capability + cost columns ---
    op.add_column(
        "model_configs",
        sa.Column(
            "supports_video_gen",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "model_configs",
        sa.Column(
            "supports_audio_gen",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "model_configs",
        sa.Column(
            "supports_tts",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "model_configs",
        sa.Column("cost_per_second", sa.Numeric(10, 6), nullable=True),
    )
    op.add_column(
        "model_configs",
        sa.Column("cost_per_character", sa.Numeric(12, 8), nullable=True),
    )
    op.add_column(
        "model_configs",
        sa.Column("cost_per_generation", sa.Numeric(10, 4), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("model_configs", "cost_per_generation")
    op.drop_column("model_configs", "cost_per_character")
    op.drop_column("model_configs", "cost_per_second")
    op.drop_column("model_configs", "supports_tts")
    op.drop_column("model_configs", "supports_audio_gen")
    op.drop_column("model_configs", "supports_video_gen")

    op.drop_column("tenants", "monthly_audio_quota")
    op.drop_column("tenants", "monthly_video_quota")
    op.drop_column("tenants", "dlp_media_gen_mask_identity")

    op.drop_index("uq_generation_jobs_idem", table_name="generation_jobs")
    op.drop_index(
        "ix_generation_jobs_status_created", table_name="generation_jobs"
    )
    op.drop_table("generation_jobs")
