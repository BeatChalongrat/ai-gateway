"""Add tenants.max_daily_spend_usd — L2 per-tenant daily media-gen spend cap.

Sprint 2B cost guard L2. Every tenant gets a hard daily USD ceiling on
real (Replicate) media generation. The submit path checks
``daily_spend + estimate <= max_daily_spend_usd`` (Redis counter keyed per
(tenant, date)) before inserting a job. Default $10.00; raise per-tenant via
this column (an audit-trailed DB change — there is NO env/admin bypass).

Mirrors migration 054 (credit_markup): a NUMERIC tenant column with a
server_default so existing rows backfill.

Revision ID: 058
Revises: 057
Create Date: 2026-06-22
"""

import sqlalchemy as sa
from alembic import op


revision = "058"
down_revision = "057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "max_daily_spend_usd",
            sa.Numeric(10, 2),
            nullable=False,
            server_default=sa.text("10.00"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tenants", "max_daily_spend_usd")
