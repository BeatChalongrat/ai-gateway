"""Phase 3 — Token packs become tenant-scoped (Q1 answer / C25).

Beat's locked Q1 answer: "Token packs belong to tenant, shared across
users within tenant." This migration adds the FK column; existing
seeded packs (Starter / Pro / Business from migration 025) stay
``NULL`` and remain **global** — available to every tenant. A tenant
can create private pricing by inserting packs with their own
``tenant_id``.

Query shape for ``portal_billing.list_packs``:

    WHERE is_active = true
      AND (tenant_id IS NULL OR tenant_id = :caller_tenant_id)

Purchase endpoint applies the same gate — non-null pack.tenant_id
must equal the caller's tenant or the pack is not visible.

Revision ID: 031
Revises: 030
Create Date: 2026-04-23
"""

from alembic import op
import sqlalchemy as sa


revision = "031"
down_revision = "030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("token_packs") as batch:
        batch.add_column(
            sa.Column(
                "tenant_id",
                sa.String(36),
                sa.ForeignKey(
                    "tenants.tenant_id",
                    name="fk_token_packs_tenant_id",
                    ondelete="RESTRICT",
                ),
                nullable=True,
            )
        )
    op.create_index(
        "idx_token_packs_tenant_id", "token_packs", ["tenant_id"]
    )


def downgrade() -> None:
    op.drop_index("idx_token_packs_tenant_id", table_name="token_packs")
    with op.batch_alter_table("token_packs") as batch:
        batch.drop_column("tenant_id")
