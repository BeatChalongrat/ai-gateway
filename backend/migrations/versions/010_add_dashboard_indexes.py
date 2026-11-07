"""Add performance indexes for dashboard queries.

Revision ID: 010
Revises: 009
Create Date: 2026-04-10
"""

from alembic import op

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("idx_tenants_status", "tenants", ["status"])
    op.create_index("idx_request_logs_created_at", "request_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("idx_request_logs_created_at", table_name="request_logs")
    op.drop_index("idx_tenants_status", table_name="tenants")
