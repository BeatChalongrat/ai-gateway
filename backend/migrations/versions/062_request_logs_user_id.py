"""Per-user token usage attribution (P0 sprint).

1. ``request_logs.user_id`` — nullable String(36), indexed. Populated by
   the orchestrator from the identity the auth layer already resolves
   (``api_keys.portal_user_id`` for portal-owned keys, or the trusted
   ``X-User-ID`` behind a shared web-chat key). NULL for admin/tenant
   keys that aren't bound to a portal user — hence no FK constraint.
   String(36) (NOT integer) because ``portal_users.id`` is a UUID string.

2. Index on ``credit_transactions.request_id`` — the join path used to
   attribute rows written BEFORE this migration (request_logs.user_id
   IS NULL there): credit_transactions(type='usage') carries user_id +
   request_id for every billable request.

Revision ID: 062
Revises: 061
Create Date: 2026-07-05
"""

import sqlalchemy as sa
from alembic import op


revision = "062"
down_revision = "061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "request_logs",
        sa.Column("user_id", sa.String(36), nullable=True),
    )
    op.create_index(
        "ix_request_logs_user_id", "request_logs", ["user_id"]
    )
    op.create_index(
        "ix_credit_transactions_request_id",
        "credit_transactions",
        ["request_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_credit_transactions_request_id", table_name="credit_transactions"
    )
    op.drop_index("ix_request_logs_user_id", table_name="request_logs")
    op.drop_column("request_logs", "user_id")
