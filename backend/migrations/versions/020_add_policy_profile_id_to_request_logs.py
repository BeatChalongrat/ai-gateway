"""Add policy_profile_id to request_logs.

Records which policy profile was actually applied to each request —
the project default OR a per-request ``policy_profile_id`` override
accepted by the validator + permission check in project_service.

Admins inspecting a request in /admin/requests/{id} now see the
effective profile without having to cross-reference the project row
or recompute from ``policy_action``.

Revision ID: 020
Revises: 019
Create Date: 2026-04-11
"""

from alembic import op
import sqlalchemy as sa


revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "request_logs",
        sa.Column("policy_profile_id", sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("request_logs", "policy_profile_id")
