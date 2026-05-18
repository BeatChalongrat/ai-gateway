"""Fill in pricing for the 24 ``model_configs`` rows that shipped at
0.0/0.0 in seed migration 037.

Background: 037 seeded a hardcoded list copied from ``_CANDIDATES``,
including 24 rows whose prices were unknown at the time (no litellm
entry; provider list API doesn't return pricing). Those rows still
have ``cost_per_input_token = 0.0`` and ``cost_per_output_token = 0.0``,
which forces ``usage_service._estimate_cost`` to fall back to the
legacy per-prefix flat rate — inaccurate for every non-OpenAI /
non-Anthropic model.

This migration UPSERTs the missing rates from the ``MANUAL_PRICING``
table in ``app.services.discovery.pricing_fetcher`` (values mirrored
here for hermeticity — migrations must not import live code). Only
rows whose price is still 0.0 are touched, and rows marked
``is_manual_override = TRUE`` are explicitly skipped so admin edits
made between 037 and now are preserved.

After this migration plus a registry reload, the routing scorer can
score every active candidate on real $/token and the billing path
can apply a real input/output split.

Revision ID: 038
Revises: 037
Create Date: 2026-05-12
"""

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "038"
down_revision = "037"
branch_labels = None
depends_on = None


def _derive_cost_tier(price: float) -> str:
    if price == 0.0:
        return "standard"
    if price <= 0.0000005:
        return "budget"
    if price <= 0.000003:
        return "standard"
    return "premium"


# (provider, model, input $/tok, output $/tok)
#
# Mirrors the subset of MANUAL_PRICING that maps to seed-shipped models.
# Bumping any value here also requires bumping the same entry in
# ``app.services.discovery.pricing_fetcher.MANUAL_PRICING`` —
# duplicated on purpose so migrations stay hermetic.
_PRICE_FILLS: list[tuple[str, str, float, float]] = [
    # OpenRouter
    ("openrouter", "openrouter/auto",                  0.000001,    0.000003),
    ("openrouter", "anthropic/claude-3.5-sonnet",      0.000003,    0.000015),
    ("openrouter", "openai/gpt-4o",                    0.0000025,   0.000010),
    ("openrouter", "meta-llama/llama-3.1-405b",        0.000003,    0.000003),
    ("openrouter", "google/gemini-pro-1.5",             0.0000035,   0.0000105),
    # Google missing variants
    ("google",     "gemini-2.0-flash-exp",             0.0000000375, 0.00000015),
    ("google",     "gemini-1.5-flash-8b",              0.0000000375, 0.00000015),
    # Groq
    ("groq",       "llama-3.3-70b-versatile",          0.00000059,  0.00000079),
    ("groq",       "mixtral-8x7b-32768",               0.00000024,  0.00000024),
    ("groq",       "gemma2-9b-it",                     0.0000002,   0.0000002),
    ("groq",       "deepseek-r1-distill-llama-70b",    0.00000075,  0.000003),
    ("groq",       "qwen-2.5-32b",                     0.00000079,  0.00000079),
    # DeepSeek
    ("deepseek",   "deepseek-coder",                   0.00000027,  0.0000011),
    # xAI
    ("xai",        "grok-2-latest",                    0.000002,    0.000010),
    ("xai",        "grok-2-mini",                      0.0000002,   0.0000004),
    # Minimax
    ("minimax",    "abab5.5-chat",                     0.00000015,  0.00000015),
    # Z.AI / Yi
    ("zai",        "yi-large",                         0.000003,    0.000003),
    ("zai",        "yi-medium",                        0.0000004,   0.0000004),
    # Mistral missing SKUs
    ("mistral",    "codestral-latest",                 0.000001,    0.000003),
    ("mistral",    "mistral-embed",                    0.0000001,   0.0),
    # Cohere missing SKUs
    ("cohere",     "command",                          0.0000005,   0.0000015),
    ("cohere",     "command-light",                    0.0000003,   0.0000006),
    # Perplexity
    ("perplexity", "llama-3.1-sonar-small-128k-online", 0.0000002,   0.0000002),
    ("perplexity", "llama-3.1-sonar-huge-128k-online",  0.000005,    0.000005),
]


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(timezone.utc)
    # Only touch rows whose price is still the seed zero AND that
    # are not an admin override. Anything else (a manual edit, or a
    # row already enriched by the discovery sync) we leave alone.
    stmt = sa.text(
        """
        UPDATE model_configs
        SET cost_per_input_token = :cin,
            cost_per_output_token = :cout,
            cost_tier = :tier,
            updated_at = :now
        WHERE provider = :provider
          AND model = :model
          AND is_manual_override = FALSE
          AND cost_per_input_token = 0.0
          AND cost_per_output_token = 0.0
        """
    )
    for provider, model, cin, cout in _PRICE_FILLS:
        bind.execute(
            stmt,
            {
                "provider": provider,
                "model": model,
                "cin": cin,
                "cout": cout,
                "tier": _derive_cost_tier(cin),
                "now": now,
            },
        )


def downgrade() -> None:
    # Restore the seed-state zeros for the rows this migration touched.
    # We can't tell which rows were 0.0 vs. genuinely-priced-as-zero
    # before the migration, so the downgrade resets every row in the
    # fill list — same as a partial restore. Admin overrides are still
    # skipped to preserve manual edits.
    bind = op.get_bind()
    stmt = sa.text(
        """
        UPDATE model_configs
        SET cost_per_input_token = 0.0,
            cost_per_output_token = 0.0,
            cost_tier = 'standard'
        WHERE provider = :provider
          AND model = :model
          AND is_manual_override = FALSE
        """
    )
    for provider, model, _cin, _cout in _PRICE_FILLS:
        bind.execute(stmt, {"provider": provider, "model": model})
