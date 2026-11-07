"""Seed the curated OpenRouter chat catalog + retire stale slugs (C1).

A prior production session curated the chat-facing model catalog directly
against the database (no migration), so a fresh DB (UAT / CI / a new
deploy) would NOT reproduce it. This migration codifies those changes so
the catalog is reproducible and idempotent:

1. Deactivate four stale slugs that the curated set replaced. They are
   already ``is_active=false`` in the seeded production DB; this is the
   declarative record so a fresh DB lands in the same state.
     * anthropic/claude-3.5-sonnet
     * anthropic/claude-3.7-sonnet:thinking
     * google/gemini-pro-1.5
     * meta-llama/llama-3.1-405b

2. Upsert the ten OpenRouter chat models that lacked a migration (the
   reasoning rows — Claude 4 family, OpenAI o-series, Gemini 2.5,
   DeepSeek R1 — were already seeded by migrations 047/048/049). All are
   ``source='manual'`` + ``is_manual_override=true`` so the discovery
   sync neither overwrites pricing/capabilities nor deactivates them.

### Fallback chains (B2)

Fallback chains are NOT a ``model_configs`` column — the
FallbackOrchestrator reads them from version-controlled code config
(``app.services.providers.model_tiers.CURATED_FALLBACKS``), which is the
single source the dispatch path actually consumes. There is therefore
nothing to seed here for chains; this migration only owns catalog rows.

### Downgrade

Re-activates the four stale slugs and deletes the ten curated rows.
Idempotent — missing rows are a no-op.

Revision ID: 050
Revises: 049
Create Date: 2026-06-01
"""

from alembic import op
import sqlalchemy as sa


revision = "050"
down_revision = "049"
branch_labels = None
depends_on = None


_STALE = [
    ("openrouter", "anthropic/claude-3.5-sonnet"),
    ("openrouter", "anthropic/claude-3.7-sonnet:thinking"),
    ("openrouter", "google/gemini-pro-1.5"),
    ("openrouter", "meta-llama/llama-3.1-405b"),
]


def upgrade() -> None:
    # 1. Retire stale slugs (idempotent — UPDATE WHERE).
    op.execute(
        sa.text(
            """
            UPDATE model_configs
               SET is_active = false, updated_at = now()
             WHERE (provider, model) IN (
                ('openrouter', 'anthropic/claude-3.5-sonnet'),
                ('openrouter', 'anthropic/claude-3.7-sonnet:thinking'),
                ('openrouter', 'google/gemini-pro-1.5'),
                ('openrouter', 'meta-llama/llama-3.1-405b')
             )
            """
        )
    )

    # 2. Upsert the curated OpenRouter chat catalog.
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
                ('openrouter', 'anthropic/claude-3.5-haiku',
                 true, true, true, 200000, 'standard', 'standard',
                 0.0000008, 0.000004, 'manual', true, true,
                 false, 'none', 'auto'),
                ('openrouter', 'anthropic/claude-opus-4.1',
                 true, true, true, 200000, 'premium', 'standard',
                 0.000015, 0.000075, 'manual', true, true,
                 false, 'none', 'auto'),
                ('openrouter', 'anthropic/claude-sonnet-4',
                 true, true, true, 1000000, 'standard', 'standard',
                 0.000003, 0.000015, 'manual', true, true,
                 false, 'none', 'auto'),
                ('openrouter', 'anthropic/claude-sonnet-4.5',
                 true, true, true, 1000000, 'standard', 'standard',
                 0.000003, 0.000015, 'manual', true, true,
                 false, 'none', 'auto'),
                ('openrouter', 'deepseek/deepseek-chat',
                 true, true, false, 131072, 'budget', 'standard',
                 0.0000002288, 0.0000009144, 'manual', true, true,
                 false, 'none', 'auto'),
                ('openrouter', 'google/gemini-2.5-flash',
                 true, true, true, 1048576, 'budget', 'standard',
                 0.0000003, 0.0000025, 'manual', true, true,
                 false, 'none', 'auto'),
                ('openrouter', 'google/gemini-2.5-pro',
                 true, true, true, 1048576, 'standard', 'standard',
                 0.00000125, 0.00001, 'manual', true, true,
                 false, 'none', 'auto'),
                ('openrouter', 'meta-llama/llama-3.3-70b-instruct',
                 true, true, false, 131072, 'budget', 'standard',
                 0.0000001, 0.00000032, 'manual', true, true,
                 false, 'none', 'auto'),
                ('openrouter', 'openai/gpt-4o',
                 true, true, true, 128000, 'standard', 'standard',
                 0.0000025, 0.00001, 'manual', true, true,
                 false, 'none', 'auto'),
                ('openrouter', 'openai/gpt-4o-mini',
                 true, true, true, 128000, 'budget', 'standard',
                 0.00000015, 0.0000006, 'manual', true, true,
                 false, 'none', 'auto')
            ON CONFLICT (provider, model) DO UPDATE SET
                supports_streaming   = EXCLUDED.supports_streaming,
                supports_tools       = EXCLUDED.supports_tools,
                supports_vision      = EXCLUDED.supports_vision,
                max_context_tokens   = EXCLUDED.max_context_tokens,
                cost_tier            = EXCLUDED.cost_tier,
                latency_tier         = EXCLUDED.latency_tier,
                cost_per_input_token = EXCLUDED.cost_per_input_token,
                cost_per_output_token= EXCLUDED.cost_per_output_token,
                source               = 'manual',
                is_manual_override   = true,
                is_active            = true,
                updated_at           = now()
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM model_configs
             WHERE provider = 'openrouter'
               AND model IN (
                'anthropic/claude-3.5-haiku',
                'anthropic/claude-opus-4.1',
                'anthropic/claude-sonnet-4',
                'anthropic/claude-sonnet-4.5',
                'deepseek/deepseek-chat',
                'google/gemini-2.5-flash',
                'google/gemini-2.5-pro',
                'meta-llama/llama-3.3-70b-instruct',
                'openai/gpt-4o',
                'openai/gpt-4o-mini'
               )
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE model_configs
               SET is_active = true, updated_at = now()
             WHERE (provider, model) IN (
                ('openrouter', 'anthropic/claude-3.5-sonnet'),
                ('openrouter', 'anthropic/claude-3.7-sonnet:thinking'),
                ('openrouter', 'google/gemini-pro-1.5'),
                ('openrouter', 'meta-llama/llama-3.1-405b')
             )
            """
        )
    )
