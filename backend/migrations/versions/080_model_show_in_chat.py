"""FIX47 Part C — a second axis on the model catalogue: ``show_in_chat``.

``is_active`` was one switch for two audiences. Turning a model off to
keep it out of the chat picker also took it away from every API caller,
so the picker showed the whole active catalogue (≈150 rows) and the
person picked from names they did not know.

``is_active``   = callable through the API (can be hundreds)
``show_in_chat`` = offered in the webchat / desktop picker (meant to be
                   a handful; an admin decides which, from the admin UI)

Defaults to TRUE so nothing disappears on upgrade — the picker looks the
same until an admin curates. The list is data, never code: a model list
in source is the class of bug that bit five times in one month (two
lists that drift apart).

Revision ID: 080
Revises: 079
Create Date: 2026-09-02
"""
import sqlalchemy as sa
from alembic import op

revision = "080"
down_revision = "079"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "model_configs",
        sa.Column("show_in_chat", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )


def downgrade() -> None:
    op.drop_column("model_configs", "show_in_chat")
