"""FIX35 Part C — the Auto router's two persistence needs.

* ``request_logs.task_category`` — the task class (an id from
  ``services/auto_router/task_taxonomy.json``) the Auto router assigned to a
  request; NULL when the caller named a model. The per-tenant 7-day ranking
  (step 2 of the router) groups on it, so it must be a column, not a log line.
* ``tenants.auto_routing_tier`` — the tenant's default cost-tier ceiling for
  Auto ('' = deployment default from system_settings
  ``auto_routing_tier_default``; else low | medium | high | xhigh | max).

Both are additive and defaulted, so an un-migrated row reads as "unknown /
default" and nothing breaks if a deployment runs the code before the
migration (the router never queries the column it cannot see — the ranking
falls back to its static defaults).

Revision ID: 079
Revises: 078
Create Date: 2026-08-31
"""

import sqlalchemy as sa
from alembic import op

revision = "079"
down_revision = "078"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "request_logs",
        sa.Column("task_category", sa.String(length=40), nullable=True),
    )
    # Ranking query: WHERE tenant_id = ? AND created_at >= now()-7d GROUP BY
    # task_category, model. The existing ix_request_logs_tenant_created
    # (tenant_id, created_at DESC) already narrows the window; this index
    # lets the GROUP BY read task_category/model without touching the row.
    op.create_index(
        "ix_request_logs_tenant_task_created",
        "request_logs",
        ["tenant_id", "task_category", "created_at"],
    )
    op.add_column(
        "tenants",
        sa.Column(
            "auto_routing_tier",
            sa.String(length=10),
            nullable=False,
            server_default=sa.text("''"),
        ),
    )
    op.get_bind().execute(
        sa.text(
            "INSERT INTO system_settings (key, value) VALUES "
            "('auto_routing_tier_default', 'high') ON CONFLICT (key) DO NOTHING"
        )
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("DELETE FROM system_settings WHERE key = 'auto_routing_tier_default'")
    )
    op.drop_column("tenants", "auto_routing_tier")
    op.drop_index("ix_request_logs_tenant_task_created", table_name="request_logs")
    op.drop_column("request_logs", "task_category")
