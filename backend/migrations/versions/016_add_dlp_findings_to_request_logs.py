"""Add DLP findings summary columns to request_logs.

Populated by the orchestrator after each round-trip. Stores only counts,
the worst action taken, and the set of categories found — raw matched
values are never persisted here.

Revision ID: 016
Revises: 015
Create Date: 2026-04-11
"""

from alembic import op
import sqlalchemy as sa


revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "request_logs",
        sa.Column(
            "dlp_input_findings",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "request_logs",
        sa.Column("dlp_input_action", sa.String(20), nullable=True),
    )
    op.add_column(
        "request_logs",
        sa.Column(
            "dlp_output_findings",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "request_logs",
        sa.Column("dlp_categories", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("request_logs", "dlp_categories")
    op.drop_column("request_logs", "dlp_output_findings")
    op.drop_column("request_logs", "dlp_input_action")
    op.drop_column("request_logs", "dlp_input_findings")
