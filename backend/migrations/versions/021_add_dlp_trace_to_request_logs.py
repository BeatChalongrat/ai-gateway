"""Add DLP trace columns to request_logs.

Stores the four message snapshots that bound the DLP pipeline plus a
JSON list of finding objects, so admins can audit exactly what was
filtered, what was sent to the provider, what came back, and what
was returned to the user.

Privacy note: ``input_raw`` and ``output_final`` contain raw user
content (potentially PII). The /admin/requests/* endpoints already
require admin auth, but operators should treat this table as
sensitive — full message bodies live here, not just metadata.

Revision ID: 021
Revises: 020
Create Date: 2026-04-11
"""

from alembic import op
import sqlalchemy as sa


revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "request_logs",
        sa.Column("input_raw", sa.Text(), nullable=True),
    )
    op.add_column(
        "request_logs",
        sa.Column("input_masked", sa.Text(), nullable=True),
    )
    op.add_column(
        "request_logs",
        sa.Column("output_raw", sa.Text(), nullable=True),
    )
    op.add_column(
        "request_logs",
        sa.Column("output_final", sa.Text(), nullable=True),
    )
    op.add_column(
        "request_logs",
        sa.Column("dlp_findings_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("request_logs", "dlp_findings_json")
    op.drop_column("request_logs", "output_final")
    op.drop_column("request_logs", "output_raw")
    op.drop_column("request_logs", "input_masked")
    op.drop_column("request_logs", "input_raw")
