"""FIX64 — where each capability flag came from: ``capability_provenance``.

A capability switch in the admin page (FIX62) and a discovery promotion
and a seed migration all land in the same boolean column, so nothing
could say whether ``supports_tools = true`` was a person's decision, a
provider's statement, a name guess, or a measured result. The per-model
capability test (FIX64 Part 2) must PROPOSE rather than overwrite a value
a person set, and the discovery sync must not re-guess a value the test
proved — both need the provenance.

One JSONB per row, keyed by capability::

    {"tools":  {"source": "probe", "at": "2026-09-05T10:00:00Z",
                "evidence": "tool_call_emitted"},
     "vision": {"source": "admin", "at": "..."},
     "tests":  {"tools": {"verdict": "proven_yes", "evidence": "...",
                          "at": "...", "applied": true}}}

``source`` ∈ {"admin", "provider", "inferred", "probe", "seed"}. Rows
upgrade with ``{}``: nobody knows who set the old values, and saying so
is more honest than back-filling a guess. Nothing else changes — every
existing flag keeps its value.

Revision ID: 083
Revises: 082
Create Date: 2026-09-05
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "083"
down_revision = "082"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "model_configs",
        sa.Column(
            "capability_provenance",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("model_configs", "capability_provenance")
