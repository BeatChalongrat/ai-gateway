"""Widen actor_key_id column to support JWT key IDs (jwt:<uuid>).

Revision ID: 013
Revises: 012
Create Date: 2026-04-10
"""

from alembic import op
import sqlalchemy as sa

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "admin_audit_log",
        "actor_key_id",
        type_=sa.String(100),
        existing_type=sa.String(36),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "admin_audit_log",
        "actor_key_id",
        type_=sa.String(36),
        existing_type=sa.String(100),
        existing_nullable=False,
    )
