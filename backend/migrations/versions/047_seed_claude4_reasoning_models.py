"""Seed Claude 4 reasoning-capable rows into ``model_configs``.

Migration 045 added the reasoning capability columns and recorded the
``anthropic_adaptive_thinking`` strategy for the Claude 4 family — but
those statements were UPDATEs, and no Claude 4 rows existed in the seed
(migration 037), so they matched nothing. The result was a capability
mismatch: chat-edge advertises ``claude-sonnet-4*`` as reasoning-capable
in its model selector, the user flips the toggle on, but the gateway has
no registry row for the model, so ``anthropic_adapter`` reads
``supports_reasoning=False`` and never injects the thinking block.

This migration inserts the rows so both sides agree. They are marked
``source='manual'`` + ``is_manual_override=True`` so the discovery sync
neither overwrites the reasoning flags nor soft-deactivates them when a
provider listing omits them (discovery honors the override per
``discovery_service``).

Pricing mirrors public Anthropic rates (USD per token):
* Sonnet 4 / 4.5  — $3 in  / $15 out  → standard / standard tiers
* Opus 4          — $15 in / $75 out  → premium  / slow     tiers
Context window is 200k for the Claude 4 family.

### Downgrade

Deletes the three rows. Idempotent on the way down — missing rows are
a no-op.

Revision ID: 047
Revises: 046
Create Date: 2026-05-29
"""

from alembic import op
import sqlalchemy as sa


revision = "047"
down_revision = "046"
branch_labels = None
depends_on = None


_MODELS = (
    "claude-sonnet-4-20250514",
    "claude-sonnet-4.5-20250514",
    "claude-opus-4-20250514",
)


def upgrade() -> None:
    # ON CONFLICT keys on the uq_model_configs_provider_model unique
    # constraint. DO UPDATE keeps the migration idempotent and also
    # repairs a row a prior manual insert created without the reasoning
    # flags set.
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
                ('anthropic', 'claude-sonnet-4-20250514',
                 true, true, true, 200000, 'standard', 'standard',
                 0.000003, 0.000015,
                 'manual', true, true,
                 true, 'anthropic_adaptive_thinking', 'auto'),
                ('anthropic', 'claude-sonnet-4.5-20250514',
                 true, true, true, 200000, 'standard', 'standard',
                 0.000003, 0.000015,
                 'manual', true, true,
                 true, 'anthropic_adaptive_thinking', 'auto'),
                ('anthropic', 'claude-opus-4-20250514',
                 true, true, true, 200000, 'premium', 'slow',
                 0.000015, 0.000075,
                 'manual', true, true,
                 true, 'anthropic_adaptive_thinking', 'auto')
            ON CONFLICT (provider, model) DO UPDATE SET
                supports_reasoning     = true,
                reasoning_strategy     = 'anthropic_adaptive_thinking',
                default_reasoning_mode = 'auto',
                is_manual_override     = true,
                is_active              = true,
                updated_at             = now()
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM model_configs
             WHERE provider = 'anthropic'
               AND model IN (
                   'claude-sonnet-4-20250514',
                   'claude-sonnet-4.5-20250514',
                   'claude-opus-4-20250514'
               )
            """
        )
    )
