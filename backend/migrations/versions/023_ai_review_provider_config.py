"""Extend AI review with provider config + cost tracking.

Three changes:
1. New columns on ``ai_review_settings`` for the dedicated review
   provider, model, encrypted API key, custom endpoint, and realtime
   sampling toggle + rate.
2. New ``ai_review_scan_runs`` table — one row per scan execution
   (manual or realtime), carrying token/cost aggregates so the admin
   can see system cost over time.
3. ``scan_run_id`` column on ``ai_review_findings`` linking each
   finding back to its scan run (already-present ``scan_run_id``
   string column is reused — this migration just keeps that schema).

Revision ID: 023
Revises: 022
Create Date: 2026-04-12
"""

from alembic import op
import sqlalchemy as sa


revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- ai_review_settings: provider config + realtime columns ---
    op.add_column(
        "ai_review_settings",
        sa.Column("review_provider", sa.String(50), nullable=True),
    )
    op.add_column(
        "ai_review_settings",
        sa.Column("review_model", sa.String(100), nullable=True),
    )
    op.add_column(
        "ai_review_settings",
        sa.Column("review_api_key_enc", sa.Text(), nullable=True),
    )
    op.add_column(
        "ai_review_settings",
        sa.Column("review_endpoint", sa.Text(), nullable=True),
    )
    op.add_column(
        "ai_review_settings",
        sa.Column(
            "realtime_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "ai_review_settings",
        sa.Column(
            "realtime_sample_rate",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("10"),
        ),
    )

    # --- ai_review_scan_runs ---
    # scan_run_id is stored as String(36) to stay consistent with the
    # rest of the codebase's id columns. The spec calls out UUID, but
    # SQLite (test engine) doesn't have a native UUID type — String is
    # portable and ``uuid.uuid4().hex`` / ``str()`` both fit.
    op.create_table(
        "ai_review_scan_runs",
        sa.Column("scan_run_id", sa.String(36), primary_key=True),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column(
            "requests_scanned",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "findings_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "prompt_tokens",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "completion_tokens",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "total_tokens",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        # NUMERIC(12,8) = up to 9999.99999999 USD per run. Real usage
        # is orders of magnitude under that, but the precision lets us
        # display "$0.00012300" without losing digits to float rounding.
        sa.Column(
            "estimated_cost_usd",
            sa.Numeric(12, 8),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'completed'"),
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("ai_review_scan_runs")
    op.drop_column("ai_review_settings", "realtime_sample_rate")
    op.drop_column("ai_review_settings", "realtime_enabled")
    op.drop_column("ai_review_settings", "review_endpoint")
    op.drop_column("ai_review_settings", "review_api_key_enc")
    op.drop_column("ai_review_settings", "review_model")
    op.drop_column("ai_review_settings", "review_provider")
