"""Seed real per-token prices for models stuck at 0 + retire dead models.

Bug being fixed: rows with ``cost_per_input_token = 0`` fall through
``usage_service._estimate_cost`` to the legacy flat table — unknown
prefixes bill at ``_LEGACY_DEFAULT_RATE`` ($1/M for BOTH input and
output) — so the credit deduction for those models is wrong in both
directions (undercharges premium models, overcharges cheap ones).

Owner-approved design (2026-08-11):
  * active models     → real price (per-1M USD map ÷ 1e6 — the column
                        stores USD **per token**; anchor: o3-mini =
                        1.1e-06/4.4e-06 = $1.10/$4.40 per 1M).
  * retired models    → ``is_active = false``, price untouched.
  * unverified prices → seeded but flagged ⚠ below; verify against the
                        provider console before trusting invoices.
  * media models (elevenlabs/replicate/mock/sora) → untouched (not
                        token-billed).
  * sranai            → untouched (self-hosted, price stays 0). Safe by
                        construction: only the rows listed below are
                        ever written.
  * openrouter        → untouched (passthrough; underlying model sets
                        the real price).

``cost_tier`` is re-derived alongside the price with the same
thresholds as ``app.services.providers.cost_tiers.derive_cost_tier``
(<= 5e-07 budget, <= 3e-06 standard, else premium) so cost-based
routing sees the corrected prices too.

Suffix-tier conflicts deliberately NOT applied (existing non-zero DB
price kept, see MR discussion): cohere command-r-08-2024 (DB $0.15/$0.60
vs command-r tier $0.50/$1.50) and openai gpt-4o-2024-05-13 (DB $5/$15
vs gpt-4o tier $2.5/$10) — both DB values are the correct dated-release
prices; overwriting from the undated tier would misbill.

Downgrade restores the exact pre-migration values recorded from the
2026-08-11 production-dev catalog audit. If an environment has drifted
from that snapshot, downgrade restores the audited baseline, not the
drifted value (standard data-migration caveat).

⚠ UNVERIFIED prices (seeded per owner's map, pending console check):
qwen-turbo, qwen-plus, qwen-max.

Revision ID: 068
Revises: 067
Create Date: 2026-08-11
"""

import sqlalchemy as sa
from alembic import op


revision = "068"
down_revision = "067"
branch_labels = None
depends_on = None


# (provider, model, old_in, old_out, old_tier, new_in, new_out, new_tier)
# old_* = audited 2026-08-11 values, used verbatim by downgrade().
_PRICE_UPDATES: list[tuple] = [
    # Mistral repriced (owner's map, exact catalog-id match).
    ("mistral", "mistral-large-latest", 3e-06, 9e-06, "standard",
     5e-07, 1.5e-06, "budget"),
    ("mistral", "mistral-small-latest", 1e-06, 3e-06, "standard",
     1.5e-07, 6e-07, "budget"),
    ("mistral", "codestral-latest", 1e-06, 3e-06, "standard",
     3e-07, 9e-07, "budget"),
    # ⚠ UNVERIFIED — was 0 (flat-$1/M fallback bug). Verify vs console.
    ("qwen", "qwen-turbo", 0.0, 0.0, "standard",
     5e-08, 2e-07, "budget"),
    ("qwen", "qwen-plus", 0.0, 0.0, "standard",
     4e-07, 1.2e-06, "budget"),
    ("qwen", "qwen-max", 0.0, 0.0, "standard",
     1.6e-06, 6.4e-06, "standard"),
]

# Dead models a user must no longer be able to pick. Price untouched.
# All were is_active = true at audit time (downgrade re-activates).
_RETIRED: list[tuple[str, str]] = [
    ("deepseek", "deepseek-chat"),
    ("deepseek", "deepseek-coder"),
    ("deepseek", "deepseek-reasoner"),
    ("kimi", "moonshot-v1-8k"),
    ("kimi", "moonshot-v1-32k"),
    ("kimi", "moonshot-v1-128k"),
    ("minimax", "abab6.5s-chat"),
    ("google", "gemini-2.0-flash-exp"),
    ("xai", "grok-2-latest"),
    ("xai", "grok-2-mini"),
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
