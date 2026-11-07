"""Add role column to api_keys.

Revision ID: 005
Revises: 004
Create Date: 2026-04-09
"""

from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.add_column(
            sa.Column("role", sa.String(20), nullable=False, server_default="user")
        )


def downgrade() -> None:
    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.drop_column("role")
