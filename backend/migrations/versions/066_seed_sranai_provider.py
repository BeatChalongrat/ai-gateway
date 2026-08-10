"""Seed the self-hosted "Sran AI" provider (P1).

Two pieces of data, no DDL:

1. A ``system_settings`` row ``provider_base_url:sranai`` holding the
   box's OpenAI-compatible endpoint. Seeded EMPTY on purpose — the
   admin fills it in on the /providers page (the add-key form sends it
   alongside the key). ``resolve_base_url`` treats an empty value as
   "not configured" and falls through to the env override.

2. ``model_configs`` rows for the models the box serves. The model ids
   below are placeholders matching the expected deployment
   (Qwen 2.5 instruct); if the box serves different ids, edit the
   VALUES here — the model list is deliberately migration-owned, not
   UI-editable (same regime as every other provider's catalog).

Pattern follows 050: idempotent upsert with ``source='manual'`` +
``is_manual_override=true`` so the discovery sweep never mutates or
deactivates these rows.

Revision ID: 066
Revises: 065
Create Date: 2026-08-10
"""

import sqlalchemy as sa
from alembic import op


revision = "066"
down_revision = "065"
branch_labels = None
depends_on = None


_SETTING_KEY = "provider_base_url:sranai"


def upgrade() -> None:
    # 1. Endpoint setting — empty until the admin configures it in the UI.
    op.execute(
        sa.text(
            """
            INSERT INTO system_settings (key, value, description)
            VALUES (
                :key,
                '',
                'Sran AI self-hosted endpoint (OpenAI-compatible base URL, e.g. http://host.docker.internal:8000/v1). Empty = not configured.'
            )
            ON CONFLICT (key) DO NOTHING
            """
        ).bindparams(key=_SETTING_KEY)
    )

    # 2. Model catalog for the box.
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
    op.execute(sa.text("DELETE FROM model_configs WHERE provider = 'sranai'"))
    op.execute(
        sa.text("DELETE FROM system_settings WHERE key = :key").bindparams(
            key=_SETTING_KEY
        )
    )
