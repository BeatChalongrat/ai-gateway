"""Add OAuth identity columns to portal_users.

Supports "Continue with Google / GitHub" on the portal signup + login
pages. Columns:

- ``oauth_provider``: 'google' | 'github' | NULL (password-only accounts).
- ``oauth_id``: the provider's stable user ID (sub / id from the OAuth
  profile). Unique within a provider.
- ``avatar_url``: optional profile picture URL from the provider.

A composite unique index on (oauth_provider, oauth_id) lets the
login-or-link lookup stay fast and prevents two accounts from sharing
the same external identity. NULLs don't conflict on Postgres unique
indexes, so password-only users coexist freely.

Revision ID: 026
Revises: 025
Create Date: 2026-04-20
"""

from alembic import op
import sqlalchemy as sa


revision = "026"
down_revision = "025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("portal_users") as batch:
        batch.add_column(sa.Column("oauth_provider", sa.String(32), nullable=True))
        batch.add_column(sa.Column("oauth_id", sa.Text(), nullable=True))
        batch.add_column(sa.Column("avatar_url", sa.Text(), nullable=True))

    op.create_index(
        "idx_portal_users_oauth",
        "portal_users",
        ["oauth_provider", "oauth_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("idx_portal_users_oauth", table_name="portal_users")
    with op.batch_alter_table("portal_users") as batch:
        batch.drop_column("avatar_url")
        batch.drop_column("oauth_id")
        batch.drop_column("oauth_provider")
