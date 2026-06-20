"""Add tenants.credit_markup — per-tenant credit billing multiplier.

Cost-based credit deduction converts each request's real USD cost into
ledger credits (``credits = ceil(cost * markup * USAGE_CREDITS_PER_USD)``).
``credit_markup`` is the admin-configurable margin per tenant:
1.0 = at cost (default for every existing row), 1.5 = 50% margin,
2.0 = 100% margin. Applied identically by the balance deduction and the
portal usage-summary display so the usage panel always matches what was
actually deducted.

Revision ID: 054
Revises: 053
Create Date: 2026-06-12
"""

import sqlalchemy as sa
from alembic import op


revision = "054"
down_revision = "053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "credit_markup",
            sa.Numeric(4, 2),
            nullable=False,
            server_default=sa.text("1.0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tenants", "credit_markup")
