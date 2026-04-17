"""Add alert delivery fields + alert_events table.

- Extends alert_rules with webhook_url (delivery target) and
  last_fired_at (cooldown guard).
- Creates alert_events to store the audit trail of fired alerts.

Revision ID: 017
Revises: 016
Create Date: 2026-04-11
"""

from alembic import op
import sqlalchemy as sa


revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "alert_rules",
        sa.Column("webhook_url", sa.String(500), nullable=True),
    )
    op.add_column(
        "alert_rules",
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "alert_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("rule_id", sa.String(36), nullable=True),
        sa.Column("alert_type", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("message", sa.String(500), nullable=False, server_default=""),
        sa.Column("details_json", sa.Text(), nullable=True),
        sa.Column("tenant_id", sa.String(36), nullable=True),
        sa.Column("project_id", sa.String(36), nullable=True),
        sa.Column("webhook_url", sa.String(500), nullable=True),
        sa.Column(
            "delivery_status",
            sa.String(20),
            nullable=False,
            server_default="log_only",
        ),
        sa.Column(
            "fired_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_alert_events_rule_fired",
        "alert_events",
        ["rule_id", "fired_at"],
    )
    op.create_index(
        "ix_alert_events_tenant_fired",
        "alert_events",
        ["tenant_id", "fired_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_alert_events_tenant_fired", table_name="alert_events")
    op.drop_index("ix_alert_events_rule_fired", table_name="alert_events")
    op.drop_table("alert_events")
    op.drop_column("alert_rules", "last_fired_at")
    op.drop_column("alert_rules", "webhook_url")
