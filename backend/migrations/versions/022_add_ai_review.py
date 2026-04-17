"""Add AI Sensitive Data Reviewer tables.

Two new tables:

- ``ai_review_settings`` — singleton feature-flag row (enabled, scope,
  scan limit, last scan timestamp). Mirrors the ``org_settings``
  singleton pattern: a fixed id=1 row created lazily on first read.

- ``ai_review_findings`` — append-only log of findings produced by
  Claude scanning ``request_logs.input_raw``. Grouped by a
  ``scan_run_id`` so the admin UI can filter findings per run.

Revision ID: 022
Revises: 021
Create Date: 2026-04-12
"""

from alembic import op
import sqlalchemy as sa


revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_review_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        # JSON array of key_ids; NULL = monitor all keys, [] = monitor none.
        sa.Column("monitored_key_ids", sa.Text(), nullable=True),
        sa.Column(
            "scan_limit",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("50"),
        ),
        sa.Column("last_scan_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "ai_review_findings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("request_id", sa.String(100), nullable=False, index=True),
        sa.Column("scan_run_id", sa.String(36), nullable=False, index=True),
        sa.Column("entity_type", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("sample_text", sa.Text(), nullable=False),
        sa.Column("risk_level", sa.String(20), nullable=False),
        sa.Column(
            "is_in_coverage",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("suggested_detector", sa.String(100), nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'new'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("ai_review_findings")
    op.drop_table("ai_review_settings")
