"""Add alert_rules table and request_logs feedback columns.

Revision ID: 014
Revises: 013
Create Date: 2026-04-10
"""

from alembic import op
import sqlalchemy as sa


revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alert_rules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("metric", sa.String(50), nullable=False),
        sa.Column("threshold", sa.Float, nullable=False),
        sa.Column("window_minutes", sa.Integer, nullable=False, server_default="5"),
        sa.Column("scope", sa.String(20), nullable=False, server_default="global"),
        sa.Column("scope_id", sa.String(36), nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_alert_rules_scope_enabled",
        "alert_rules",
        ["scope", "enabled"],
    )

    op.add_column(
        "request_logs",
        sa.Column("feedback_rating", sa.Integer, nullable=True),
    )
    op.add_column(
        "request_logs",
        sa.Column("feedback_comment", sa.String(1000), nullable=True),
    )
    op.add_column(
        "request_logs",
        sa.Column(
            "feedback_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("request_logs", "feedback_at")
    op.drop_column("request_logs", "feedback_comment")
    op.drop_column("request_logs", "feedback_rating")
    op.drop_index("ix_alert_rules_scope_enabled", table_name="alert_rules")
    op.drop_table("alert_rules")
