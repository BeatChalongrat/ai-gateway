"""Add admin_audit_log table.

Revision ID: 007
Revises: 006
Create Date: 2026-04-09
"""

from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_audit_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("actor_key_id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("action", sa.String(100), nullable=False, index=True),
        sa.Column("resource_type", sa.String(50), nullable=False),
        sa.Column("resource_id", sa.String(36), nullable=False),
        sa.Column("details", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("admin_audit_log")
