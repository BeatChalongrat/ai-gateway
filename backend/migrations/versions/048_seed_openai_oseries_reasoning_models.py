"""Seed OpenAI o-series reasoning rows into ``model_configs``.

Auto Thinking Mode needs at least one reasoning-capable model whose
provider key actually works in this deployment. The Claude 4 rows from
migration 047 are reasoning-capable but the Anthropic key here is a
placeholder, and the DeepSeek / Groq R1 rows have no key at all — so a
complex ``auto`` turn that resolves to any of them fails at the provider.

The OpenAI key IS valid and its org has o-series access (verified live:
``o3-mini`` and ``o4-mini`` both answer). Seeding them gives the router a
reasoning model it can actually reach, so:
  * chat-edge's Auto resolver can prefer o3-mini for complex turns
    (it is listed before the keyless Claude row in DefaultModels), and
  * the gateway sends ``reasoning_effort`` and attributes
    ``reasoning_tokens`` correctly.

Note: OpenAI's Chat Completions API returns a reasoning *token count*
but no reasoning *text*, so these models think server-side without
populating a visible ThinkingBlock — an OpenAI limitation, handled
gracefully (no empty block is shown because no reasoning_delta /
analysis_status frames are emitted). Visible thinking requires an
Anthropic / DeepSeek / Gemini key, which this environment lacks.

Pricing mirrors public OpenAI rates (USD per token):
* o3-mini — $1.10 in / $4.40 out → standard / slow tiers
* o4-mini — $1.10 in / $4.40 out → standard / slow tiers
Context window is 200k for the o-series.

Marked ``source='manual'`` + ``is_manual_override=True`` so the discovery
sync neither overwrites the reasoning flags nor soft-deactivates the rows.

### Downgrade

Deletes the two rows. Idempotent — missing rows are a no-op.

Revision ID: 048
Revises: 047
Create Date: 2026-05-29
"""

from alembic import op
import sqlalchemy as sa


revision = "048"
down_revision = "047"
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
                ('openai', 'o3-mini',
                 true, true, false, 200000, 'standard', 'slow',
                 0.0000011, 0.0000044,
                 'manual', true, true,
                 true, 'openai_responses_reasoning', 'auto'),
                ('openai', 'o4-mini',
                 true, true, false, 200000, 'standard', 'slow',
                 0.0000011, 0.0000044,
                 'manual', true, true,
                 true, 'openai_responses_reasoning', 'auto')
            ON CONFLICT (provider, model) DO UPDATE SET
                supports_reasoning     = true,
                reasoning_strategy     = 'openai_responses_reasoning',
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
             WHERE provider = 'openai'
               AND model IN ('o3-mini', 'o4-mini')
            """
        )
    )
