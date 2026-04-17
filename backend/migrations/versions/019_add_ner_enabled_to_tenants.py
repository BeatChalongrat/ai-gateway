"""Add ner_enabled column to tenants.

Per-tenant NER flag that coexists with the global OrgSettingsORM.ner_enabled
toggle. Resolution is an OR at request time:
    ner_active = tenant.ner_enabled or org_settings.ner_enabled

So enabling at the org level flips NER on for every tenant, OR an
individual tenant can opt in without affecting others. Opting a
single tenant OUT while the org flag is on is intentionally NOT
supported in v1 — keeps the rule simple and auditable.

Revision ID: 019
Revises: 018
Create Date: 2026-04-11
"""

from alembic import op
import sqlalchemy as sa


revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "ner_enabled",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tenants", "ner_enabled")
