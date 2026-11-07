"""FIX85 — an endpoint belongs to the key, not to the provider.

``provider_keys.base_url``: the endpoint THIS key talks to. NULL means
"use the provider-level ``system_settings`` row" — exactly what every
existing row did before this column existed, so no backfill: a
deployment where every value is NULL behaves as it did yesterday.

``provider_keys.last_tested_base_url``: the endpoint a key's stored
verdict was reached against. The incident this round answers is a key
that was tested against one box and reported "invalid" while the
operator was thinking of the other; the badge can now say which box
said no.

``model_configs.served_by_keys``: which keys' endpoints reported this
model when the catalogue was fetched (``{key_id: {"base_url", "label",
"at"}}``). ``{}`` = never recorded, and dispatch treats such a row as it
always has (any key of the provider). Not a second catalogue: a column on
the row the catalogue already is.

Reversible: the downgrade drops the three columns; the rows and every
other value survive untouched.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "087"
down_revision = "086"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "provider_keys",
        sa.Column("base_url", sa.String(512), nullable=True),
    )
    op.add_column(
        "provider_keys",
        sa.Column("last_tested_base_url", sa.String(512), nullable=True),
    )
    op.add_column(
        "model_configs",
        sa.Column(
            "served_by_keys",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("model_configs", "served_by_keys")
    op.drop_column("provider_keys", "last_tested_base_url")
    op.drop_column("provider_keys", "base_url")
