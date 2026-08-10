"""Remove the Sran AI placeholder model rows (auto-discover takes over).

066 seeded two guessed model ids so the provider card had something to
show before discovery existed. Now that ``discover_sranai`` populates
``model_configs`` from the box's real ``GET /models`` on key-add (and
the daily sweep), the placeholders would linger as ghost entries next
to the discovered rows — models the box may not actually serve.

Deletes ONLY the two seeded ids (idempotent — a no-op if already gone
or never applied). Keeps 066's ``provider_base_url:sranai`` setting
row untouched. After this migration a fresh install shows Sran AI with
zero models until an endpoint + key are saved — which is the truthful
state (the /providers card survives via the SELF_HOSTED_PROVIDERS
backstop in the admin route, not via model rows).

Revision ID: 067
Revises: 066
Create Date: 2026-08-10
"""

import sqlalchemy as sa
from alembic import op


revision = "067"
down_revision = "066"
branch_labels = None
depends_on = None


_PLACEHOLDER_MODELS = ("qwen2.5-32b-instruct", "qwen2.5-3b-instruct")


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM model_configs
             WHERE provider = 'sranai'
               AND model IN ('qwen2.5-32b-instruct', 'qwen2.5-3b-instruct')
               AND source = 'manual'
            """
        )
    )


def downgrade() -> None:
    # Restore 066's seed rows verbatim.
    op.execute(
        sa.text(
            """
            INSERT INTO model_configs (
                provider, model,
                supports_streaming, supports_tools, supports_vision,
                max_context_tokens, cost_tier, latency_tier,
                cost_per_input_token, cost_per_output_token,
                source, is_manual_override, is_active,
                supports_reasoning, reasoning_strategy, default_reasoning_mode
            )
            VALUES
                ('sranai', 'qwen2.5-32b-instruct',
                 true, false, false, 32768, 'standard', 'standard',
                 0.0, 0.0, 'manual', true, true,
                 false, 'none', 'auto'),
                ('sranai', 'qwen2.5-3b-instruct',
                 true, false, false, 32768, 'budget', 'fast',
                 0.0, 0.0, 'manual', true, true,
                 false, 'none', 'auto')
            ON CONFLICT (provider, model) DO NOTHING
            """
        )
    )
