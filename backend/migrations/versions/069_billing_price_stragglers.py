"""Close the last price=0 stragglers left open by migration 068.

068 seeded real prices for every active model the owner's price map
covered and retired the dead ones, but four catalog rows had neither a
map price nor a retirement decision, and one had an ambiguous id
match. Owner's follow-up decision (2026-08-12):

  * ``openai/chat-latest``  → price at the gpt-4o tier ($2.50/$10.00
    per 1M → 2.5e-06/1e-05 per token; the alias serves a 4o-class
    model), stays active. cost_tier re-derived → "standard"
    (unchanged) per ``cost_tiers.derive_cost_tier``.
  * retire, price untouched: ``anthropic/claude-3-5-sonnet-20241022``,
    ``google/gemini-1.5-pro``, ``google/gemini-1.5-flash`` (all price
    0 — were billing at the legacy flat default), and
    ``minimax/abab5.5-chat`` (068's retired list said
    "abab5.5s-chat"; the catalog id has no "s" — now retired under
    its real id).

After this migration NO active chat model rides the flat-$1/M
fallback: the only active price-0 rows left are the media models
(elevenlabs / replicate / mock / sora — not token-billed, deliberate)
and sranai has no rows at all. ``openrouter/auto`` stays -1/-1
(passthrough sentinel, not 0).

Downgrade restores the audited 2026-08-12 values verbatim:
chat-latest back to 0/0 ("standard" tier), the four retirees
re-activated.

Revision ID: 069
Revises: 068
Create Date: 2026-08-12
"""

import sqlalchemy as sa
from alembic import op


revision = "069"
down_revision = "068"
branch_labels = None
depends_on = None


# (provider, model, old_in, old_out, old_tier, new_in, new_out, new_tier)
_PRICE_UPDATES: list[tuple] = [
    ("openai", "chat-latest", 0.0, 0.0, "standard",
     2.5e-06, 1e-05, "standard"),
]

# All were is_active = true at audit time (downgrade re-activates).
_RETIRED: list[tuple[str, str]] = [
    ("anthropic", "claude-3-5-sonnet-20241022"),
    ("google", "gemini-1.5-pro"),
    ("google", "gemini-1.5-flash"),
    ("minimax", "abab5.5-chat"),
]


def upgrade() -> None:
    conn = op.get_bind()
    for provider, model, _oi, _oo, _ot, new_in, new_out, new_tier in (
        _PRICE_UPDATES
    ):
        conn.execute(
            sa.text(
                """
                UPDATE model_configs
                   SET cost_per_input_token = :cin,
                       cost_per_output_token = :cout,
                       cost_tier = :tier,
                       updated_at = now()
                 WHERE provider = :provider AND model = :model
                """
            ),
            {"cin": new_in, "cout": new_out, "tier": new_tier,
             "provider": provider, "model": model},
        )
    for provider, model in _RETIRED:
        conn.execute(
            sa.text(
                """
                UPDATE model_configs
                   SET is_active = false,
                       updated_at = now()
                 WHERE provider = :provider AND model = :model
                """
            ),
            {"provider": provider, "model": model},
        )


def downgrade() -> None:
    conn = op.get_bind()
    for provider, model, old_in, old_out, old_tier, _ni, _no, _nt in (
        _PRICE_UPDATES
    ):
        conn.execute(
            sa.text(
                """
                UPDATE model_configs
                   SET cost_per_input_token = :cin,
                       cost_per_output_token = :cout,
                       cost_tier = :tier,
                       updated_at = now()
                 WHERE provider = :provider AND model = :model
                """
            ),
            {"cin": old_in, "cout": old_out, "tier": old_tier,
             "provider": provider, "model": model},
        )
    for provider, model in _RETIRED:
        conn.execute(
            sa.text(
                """
                UPDATE model_configs
                   SET is_active = true,
                       updated_at = now()
                 WHERE provider = :provider AND model = :model
                """
            ),
            {"provider": provider, "model": model},
        )
