"""Catalog metadata accuracy fixes (conservative batch).

Investigation results behind this migration (2026-08-12):

1. ``supports_streaming = false`` on every mistral / cohere /
   perplexity row — KEPT AS-IS, deliberately. Those three providers
   have NO live adapter at all: ``provider_dispatcher`` registers 12
   adapters (openai, anthropic, openrouter, minimax, qwen, kimi, zai,
   google, groq, deepseek, xai, sranai) and the mistral/cohere/
   perplexity implementations are stubs in ``providers/_pending/``
   that raise ProviderNotImplementedError. Until a live adapter
   exists, no flag value is "true" in any meaningful sense — flipping
   to true would advertise streaming for models that cannot serve at
   all. When those adapters land, set the flag from the adapter's real
   ``supports_streaming`` capability, not here. (Consequence worth
   knowing: 070's mistral-medium-latest / command-a / sonar /
   sonar-pro are catalog-ready but natively unroutable until then;
   they stay out of /v1/models because a provider without an adapter
   can never hold a validated key.)

2. ``cohere/command-a`` context window 128000 → 256000. The 070
   insert mirrored the command-r-plus template's 128k; Cohere's own
   documentation and launch coverage confirm Command A ships a 256k
   context window (docs.cohere.com/docs/command-a, verified
   2026-08-12).

3. kimi "slow" latency tag — cosmetic, left alone per owner.

Data-only, reversible: downgrade restores the audited 128000.
Prices, retirement flags, media rows and sranai are untouched.

Revision ID: 071
Revises: 070
Create Date: 2026-08-12
"""

import sqlalchemy as sa
from alembic import op


revision = "071"
down_revision = "070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE model_configs
               SET max_context_tokens = 256000,
                   updated_at = now()
             WHERE provider = 'cohere' AND model = 'command-a'
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE model_configs
               SET max_context_tokens = 128000,
                   updated_at = now()
             WHERE provider = 'cohere' AND model = 'command-a'
            """
        )
    )
