"""Admin-configurable Auto-routing defaults.

``model="auto"`` resolved through ``smart_router``'s "cheapest capable"
rule, which in this catalog elects ``gpt-3.5-turbo`` (cost_tier=budget,
supports_tools=true) for ordinary short turns. A budget model handed the
full memory tool set calls ``save_memory`` on questions that are not
memory requests at all — the over-trigger that prompt guards alone could
not fix, since the guard is a request and the model simply doesn't
follow it.

This seeds two settings so an operator picks the Auto model from the
admin UI without a deploy:

    auto_default_model      the model Auto prefers
    auto_default_fallbacks  ordered chain used when the default isn't
                            routable (no provider key, row deactivated,
                            filtered out by a capability hint)

Values are OpenAI-only on purpose: ``provider_keys`` currently holds one
active, test-valid key (openai) and every other provider's env var is a
dev placeholder, so an anthropic default would fall through on every
turn. Change it in the UI once another key is live.

``trial_model_tier`` ("Budget maximum tier") is raised budget→standard
in the same step, but ONLY if it still carries 024's seeded value: the
settings API validates that the Auto default's cost tier does not exceed
that ceiling, and gpt-4o is standard-tier. Nothing reads
``trial_model_tier`` at runtime today, so this is a consistency fix for
the validation rule rather than a behaviour change. An operator who had
already chosen a tier keeps their choice (and simply can't pick a
standard-tier Auto default until they raise it).

Revision ID: 073
Revises: 072
Create Date: 2026-08-15
"""

import sqlalchemy as sa
from alembic import op


revision = "073"
down_revision = "072"
branch_labels = None
depends_on = None


_SETTINGS = sa.table(
    "system_settings",
    sa.column("key", sa.Text()),
    sa.column("value", sa.Text()),
    sa.column("description", sa.Text()),
)

_NEW_ROWS = [
    {
        "key": "auto_default_model",
        "value": "gpt-4o",
        "description": (
            "Model that 'auto' prefers. Must be active and support tool "
            "calling, and its cost tier may not exceed trial_model_tier."
        ),
    },
    {
        "key": "auto_default_fallbacks",
        "value": "gpt-4.1,gpt-4o-mini",
        "description": (
            "Comma-separated, in order: tried when auto_default_model has "
            "no usable provider key or is filtered out for this turn."
        ),
    },
]


def upgrade() -> None:
    conn = op.get_bind()
    existing = {
        row[0]
        for row in conn.execute(
            sa.text(
                "SELECT key FROM system_settings WHERE key IN "
                "('auto_default_model', 'auto_default_fallbacks')"
            )
        )
    }
    rows = [r for r in _NEW_ROWS if r["key"] not in existing]
    if rows:
        op.bulk_insert(_SETTINGS, rows)

    # Keep the shipped default valid against the max-tier ceiling — see
    # the module docstring. Only touches the untouched 024 seed value.
    conn.execute(
        sa.text(
            "UPDATE system_settings SET value = 'standard' "
            "WHERE key = 'trial_model_tier' AND value = 'budget'"
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "DELETE FROM system_settings WHERE key IN "
            "('auto_default_model', 'auto_default_fallbacks')"
        )
    )
    conn.execute(
        sa.text(
            "UPDATE system_settings SET value = 'budget' "
            "WHERE key = 'trial_model_tier' AND value = 'standard'"
        )
    )
