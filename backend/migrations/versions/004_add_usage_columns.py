"""Add token/usage columns to request_logs.

Revision ID: 004
Revises: 003
Create Date: 2026-04-09
"""

from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.add_column(
            sa.Column("prompt_tokens", sa.Integer, server_default=sa.text("0"))
        )
        batch_op.add_column(
            sa.Column("completion_tokens", sa.Integer, server_default=sa.text("0"))
        )
        batch_op.add_column(
            sa.Column("total_tokens", sa.Integer, server_default=sa.text("0"))
        )
        batch_op.add_column(
            sa.Column("error_code", sa.String(100), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.drop_column("error_code")
        batch_op.drop_column("total_tokens")
        batch_op.drop_column("completion_tokens")
        batch_op.drop_column("prompt_tokens")
