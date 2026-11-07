"""Correct deepseek/deepseek-r1 supports_tools -> false (reasoning model).

``deepseek/deepseek-r1`` (OpenRouter) was seeded by migration 049 with
``supports_tools=true``. R1 is a reasoning model that streams its chain of
thought instead of emitting ``tool_calls`` — it does NOT reliably support
function calling. With ``supports_tools=true`` the gateway flags it
``image_generation_via_tools`` and Phase D smart image routing treats it as
tool-capable, so it does NOT override it for an image request — and a user
on R1 asking for a picture gets reasoning text instead of an image.

Setting ``supports_tools=false`` makes Phase D route those turns to a
tool-capable fallback (gpt-4o-mini), so the image is actually produced.

Verified live before this fix: R1 + "draw me a cat" streamed reasoning, no
tool call, no image. The OTHER reasoning rows are left unchanged because
they DO support tools: o3-mini / o4-mini (openai_responses_reasoning),
claude-4 family + claude-3.7-sonnet:thinking, gemini-2.5, deepseek-chat.
o1 / o1-mini already carry supports_tools=false. The deepseek/groq R1 SEED
fallbacks (deepseek-reasoner, deepseek-r1-distill-llama-70b) are already
supports_tools=false in both DB and SEED_CANDIDATES.

Revision ID: 051
Revises: 050
Create Date: 2026-06-10
"""

from alembic import op


revision = "051"
down_revision = "050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent: no-op if the row is missing or already false. Keep
    # is_manual_override=true (already set on this row) so the discovery
    # sync won't revert the capability back to the provider default.
    op.execute(
        """
        UPDATE model_configs
        SET supports_tools = false,
            is_manual_override = true,
            updated_at = NOW()
        WHERE provider = 'openrouter'
          AND model = 'deepseek/deepseek-r1'
          AND supports_tools = true
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE model_configs
        SET supports_tools = true,
            updated_at = NOW()
        WHERE provider = 'openrouter'
          AND model = 'deepseek/deepseek-r1'
        """
    )
