"""Add reasoning capability fields to ``model_configs``.

Foundation for Thinking / Reasoning Mode (T1). Adds three columns and
backfills the rows the gateway already seeds for known reasoning-
capable models. Adapters do not consume these columns until T2.

* ``supports_reasoning``        — bool flag, mirrors the existing
                                  ``supports_streaming/tools/vision``
                                  capability columns.
* ``reasoning_strategy``        — varchar tag naming the adapter
                                  pathway used to translate the public
                                  ``reasoning`` config into provider-
                                  native shape. One of:
                                    * ``none``
                                    * ``openai_responses_reasoning``
                                      (OpenAI o-series ``reasoning_effort``)
                                    * ``anthropic_adaptive_thinking``
                                      (Anthropic ``thinking`` block)
                                    * ``gemini_thinking_level``
                                      (Gemini 2.5+ ``thinkingConfig``)
                                    * ``openai_compatible_reasoning``
                                      (DeepSeek-R1, Groq R1 distill,
                                      OpenRouter — parse
                                      ``reasoning_content`` on the
                                      OpenAI-compatible body)
                                    * ``custom``
* ``default_reasoning_mode``    — varchar; default mode the gateway
                                  picks when the caller sends a
                                  reasoning-capable model without an
                                  explicit ``ReasoningConfig.mode``.
                                  One of ``off / auto / low / medium / high``.

### Seed backfill

The seed in migration 037 already includes ``deepseek-reasoner`` and
``deepseek-r1-distill-llama-70b`` — the two reasoning-capable models
that ship in the gateway today. Both rows are flipped to
``supports_reasoning=true`` here.

The other UPDATE statements (OpenAI o-series, Claude 4, Gemini 2.5)
match nothing in the current seed but are no-ops if the rows are
absent — kept so the migration is the canonical place to record
which models are reasoning-capable when the discovery sync surfaces
them. Idempotent.

### Downgrade

Drops the three columns. No data preserved.

Revision ID: 045
Revises: 044
Create Date: 2026-05-28
"""

from alembic import op
import sqlalchemy as sa


revision = "045"
down_revision = "044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "model_configs",
        sa.Column(
            "supports_reasoning",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "model_configs",
        sa.Column(
            "reasoning_strategy",
            sa.String(length=80),
            nullable=False,
            server_default=sa.text("'none'"),
        ),
    )
    op.add_column(
        "model_configs",
        sa.Column(
            "default_reasoning_mode",
            sa.String(length=20),
            nullable=True,
            server_default=sa.text("'auto'"),
        ),
    )

    # --- Seed backfill ---------------------------------------------------
    # Idempotent: UPDATE … WHERE model IN (…) does nothing when a row
    # is absent. The seed in migration 037 only includes
    # ``deepseek-reasoner`` and ``deepseek-r1-distill-llama-70b``; the
    # other names match nothing today but are recorded here so that
    # discovery sync rows for o3 / Claude 4 / Gemini 2.5 inherit the
    # right strategy when they first appear.

    # OpenAI o-series — Reasoning via ``reasoning_effort``.
    op.execute(
        sa.text(
            """
            UPDATE model_configs
               SET supports_reasoning = true,
                   reasoning_strategy = 'openai_responses_reasoning',
                   default_reasoning_mode = 'medium'
             WHERE provider = 'openai'
               AND model IN (
                   'o3', 'o3-mini', 'o3-2025-04-16',
                   'o3-mini-2025-01-31', 'o4-mini', 'o3-pro'
               )
            """
        )
    )

    # Anthropic Claude 4+ — adaptive thinking blocks.
    op.execute(
        sa.text(
            """
            UPDATE model_configs
               SET supports_reasoning = true,
                   reasoning_strategy = 'anthropic_adaptive_thinking',
                   default_reasoning_mode = 'auto'
             WHERE provider = 'anthropic'
               AND model IN (
                   'claude-sonnet-4-20250514',
                   'claude-opus-4-20250514',
                   'claude-sonnet-4.5-20250514'
               )
            """
        )
    )

    # DeepSeek-R1 family + Groq R1 distill — OpenAI-compatible bodies
    # with a ``reasoning_content`` field. Both rows exist in seed 037,
    # so this UPDATE actually mutates state.
    op.execute(
        sa.text(
            """
            UPDATE model_configs
               SET supports_reasoning = true,
                   reasoning_strategy = 'openai_compatible_reasoning',
                   default_reasoning_mode = 'medium'
             WHERE model IN (
                   'deepseek-reasoner',
                   'deepseek-r1-distill-llama-70b'
               )
            """
        )
    )

    # Gemini 2.5+ / Gemini 3 — thinkingConfig.thinkingLevel. No matching
    # rows in the current seed; recorded here for forward compat with
    # discovery sync.
    op.execute(
        sa.text(
            """
            UPDATE model_configs
               SET supports_reasoning = true,
                   reasoning_strategy = 'gemini_thinking_level',
                   default_reasoning_mode = 'auto'
             WHERE provider = 'google'
               AND (model LIKE 'gemini-2.5%' OR model LIKE 'gemini-3%')
            """
        )
    )


def downgrade() -> None:
    op.drop_column("model_configs", "default_reasoning_mode")
    op.drop_column("model_configs", "reasoning_strategy")
    op.drop_column("model_configs", "supports_reasoning")
