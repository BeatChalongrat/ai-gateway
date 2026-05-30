"""Seed OpenRouter + Gemini reasoning rows into ``model_configs``.

The reasoning adapters for OpenRouter (``include_reasoning`` +
``reasoning.effort``, parsed via the shared OpenAI-compatible
``reasoning_content`` path) and Gemini (``thinkingConfig.thinkingLevel``
+ ``thought:true`` part splitting) are fully built but migration 045
documented them without seeding any rows — so the coded support was
unreachable (the audit's "0 OpenRouter / 0 Gemini reasoning rows" gap).

This seeds:

OpenRouter (provider key is VALID in this deployment, so these are
actually reachable, and unlike OpenAI o-series they emit visible
``reasoning_content`` — R1 thinks out loud):
  * deepseek/deepseek-r1                 — DeepSeek R1 reasoning
  * anthropic/claude-3.7-sonnet:thinking — Claude extended thinking via OR
  strategy ``openai_compatible_reasoning``.

Gemini 2.5 (adapters ready; this deployment has no Google key yet, so
these are seeded for completeness/parity — a complex ``auto`` turn won't
route to them until a key is added, and the key-aware reasoning router
(P0) already skips keyless providers):
  * gemini-2.5-flash — fast thinking tier
  * gemini-2.5-pro   — deep thinking tier
  strategy ``gemini_thinking_level``.

Pricing mirrors public list rates (USD per token); approximate and
overridable via the admin UI. Marked ``source='manual'`` +
``is_manual_override=True`` so discovery sync neither overwrites the
reasoning flags nor deactivates the rows.

### Downgrade

Deletes the four rows. Idempotent — missing rows are a no-op.

Revision ID: 049
Revises: 048
Create Date: 2026-05-31
"""

from alembic import op
import sqlalchemy as sa


revision = "049"
down_revision = "048"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
                ('openrouter', 'deepseek/deepseek-r1',
                 true, true, false, 64000, 'cheap', 'slow',
                 0.00000055, 0.00000219,
                 'manual', true, true,
                 true, 'openai_compatible_reasoning', 'auto'),
                ('openrouter', 'anthropic/claude-3.7-sonnet:thinking',
                 true, true, true, 200000, 'standard', 'standard',
                 0.000003, 0.000015,
                 'manual', true, true,
                 true, 'openai_compatible_reasoning', 'auto'),
                ('google', 'gemini-2.5-flash',
                 true, true, true, 1000000, 'cheap', 'fast',
                 0.0000003, 0.0000025,
                 'manual', true, true,
                 true, 'gemini_thinking_level', 'auto'),
                ('google', 'gemini-2.5-pro',
                 true, true, true, 1000000, 'standard', 'standard',
                 0.00000125, 0.00001,
                 'manual', true, true,
                 true, 'gemini_thinking_level', 'auto')
            ON CONFLICT (provider, model) DO UPDATE SET
                supports_reasoning     = true,
                reasoning_strategy     = EXCLUDED.reasoning_strategy,
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
             WHERE (provider = 'openrouter'
                    AND model IN ('deepseek/deepseek-r1',
                                  'anthropic/claude-3.7-sonnet:thinking'))
                OR (provider = 'google'
                    AND model IN ('gemini-2.5-flash', 'gemini-2.5-pro'))
            """
        )
    )
