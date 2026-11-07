"""Phase 3.5 — add portal_users.preferred_language.

Enables locale-aware transactional email (Resend via
``app.services.email``). Default is ``'en'``; supported languages
today are ``'en'`` and ``'th'``. Unknown values fall back to English
at render time, so this column is a soft hint — no enum constraint,
no CHECK, just a plain String(8) so adding French / Japanese later
needs zero DDL.

Revision ID: 032
Revises: 031
Create Date: 2026-04-23
"""

from alembic import op
import sqlalchemy as sa


revision = "032"
down_revision = "031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("portal_users") as batch:
        batch.add_column(
            sa.Column(
                "preferred_language",
                sa.String(8),
                nullable=False,
                server_default=sa.text("'en'"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("portal_users") as batch:
        batch.drop_column("preferred_language")
