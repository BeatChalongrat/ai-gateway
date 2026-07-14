"""Per-user OAuth tokens for MCP connectors (Desktop Phase 7).

``connector_tokens`` stores the provider tokens the connector OAuth
broker acquires on the user's behalf. Both token columns are AES-256-GCM
blobs (``security.encrypt_provider_key``) — never plaintext. Unique
``(portal_user_id, connector_id)`` so re-connecting a connector upserts
rather than accumulating rows.

Revision ID: 063
Revises: 062
Create Date: 2026-07-13
"""

import sqlalchemy as sa
from alembic import op


revision = "063"
down_revision = "062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connector_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=True),
        sa.Column(
            "portal_user_id",
            sa.String(36),
            sa.ForeignKey(
                "portal_users.id", name="fk_connector_tokens_portal_user"
            ),
            nullable=False,
        ),
        sa.Column("connector_id", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "portal_user_id",
            "connector_id",
            name="uq_connector_tokens_user_connector",
        ),
    )
    op.create_index(
        "ix_connector_tokens_portal_user_id",
        "connector_tokens",
        ["portal_user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_connector_tokens_portal_user_id", table_name="connector_tokens"
    )
    op.drop_table("connector_tokens")
