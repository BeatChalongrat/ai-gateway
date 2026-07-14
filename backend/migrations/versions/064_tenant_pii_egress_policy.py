"""Per-tenant raw-egress policy for the two intentional unmasked channels
(Gateway Phase 9).

``search_pii_egress`` / ``media_pii_egress``: ``allow`` (default — today's
behavior: identity PII may egress raw to search/media providers, audited)
or ``block`` (fail closed on ANY identity PII for compliance-strict
tenants). The credential/secret class always blocks regardless of these.

Revision ID: 064
Revises: 063
Create Date: 2026-07-14
"""

import sqlalchemy as sa
from alembic import op


revision = "064"
down_revision = "063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "search_pii_egress",
            sa.String(8),
            nullable=False,
            server_default="allow",
        ),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "media_pii_egress",
            sa.String(8),
            nullable=False,
            server_default="allow",
        ),
    )


def downgrade() -> None:
    op.drop_column("tenants", "media_pii_egress")
    op.drop_column("tenants", "search_pii_egress")
