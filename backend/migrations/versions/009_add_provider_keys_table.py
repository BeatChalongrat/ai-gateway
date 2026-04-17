"""Add provider_keys table for multi-key support.

Revision ID: 009
Revises: 008_provider_key
Create Date: 2026-04-10
"""

from alembic import op
import sqlalchemy as sa

revision = "009"
down_revision = "008_provider_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_keys",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider_name", sa.String(50), nullable=False, index=True),
        sa.Column("label", sa.String(100), nullable=True),
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_status", sa.String(20), nullable=True),
        sa.Column("last_test_latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("provider_name", "label", name="uq_provider_key_label"),
    )


def downgrade() -> None:
    op.drop_table("provider_keys")
