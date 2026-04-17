"""Add api_key_encrypted and api_key_set_at to providers table.

Revision ID: 008_provider_key
Revises: 007_add_admin_audit_log
Create Date: 2026-04-10
"""

from alembic import op
import sqlalchemy as sa

revision = "008_provider_key"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("providers", sa.Column("api_key_encrypted", sa.String(500), nullable=True))
    op.add_column("providers", sa.Column("api_key_set_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("providers", "api_key_set_at")
    op.drop_column("providers", "api_key_encrypted")
