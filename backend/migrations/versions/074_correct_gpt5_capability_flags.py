"""Correct the gpt-5 catalog tool flags (supports_tools only).

Every ``gpt-5*`` row was seeded ``supports_tools=false`` — wrong
against live behaviour (2026-08-17 audit): tool calling works on every
gpt-5 generation, proven live for 5, 5.1, 5.4 and (with the
reasoning_effort:"none" pin) 5.6. This flips ``supports_tools=true``
for all active openai ``gpt-5*`` rows.

Effects:
* the auto-router's tool-capability override
  (orchestrator._enforce_tool_capable_model) stops swapping a gpt-5
  pick away on tool-carrying auto turns — note the admin Auto
  preference (settings ``auto_default_model``=gpt-4o, migration 073)
  still wins first, so the default Auto pick does not change;
* the models endpoint stops advertising these rows as tool-blind.

``supports_reasoning`` is DELIBERATELY left false on every gpt-5 row,
although the models demonstrably reason (luna logged 55 reasoning
tokens through our own accounting). Flipping it is blocked on an
adapter fix: with the flag true, the adapter injects
``reasoning_effort`` for any non-off mode — and web-chat sends
``mode=auto`` with tools on every turn, while OpenAI 400s
"Function tools with reasoning_effort are not supported" for the
whole generation on /chat/completions (proven live against
gpt-5.4-mini AND gpt-5.6, 2026-08-17). The existing effort:"none"
pin covers only the 5.6 family and only when no effort was already
chosen, so it does not save the other generations. Once the adapter
pins effort to "none" for tool-carrying turns across the gpt-5
family (ordered to win over the reasoning block), a follow-up
migration can flip supports_reasoning + reasoning_strategy — and
that unlocks real visible reasoning summaries: /v1/responses was
live-verified (2026-08-17) to return summary text for gpt-5.4-mini
through this stack, with no org verification required (unlike the
o-series).

Revision ID: 074
Revises: 073
Create Date: 2026-08-17
"""

import sqlalchemy as sa
from alembic import op


revision = "074"
down_revision = "073"
branch_labels = None
depends_on = None

_TOOLS_WHERE = "provider = 'openai' AND model LIKE 'gpt-5%'"


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            f"UPDATE model_configs SET supports_tools = TRUE, "
            f"updated_at = NOW() WHERE {_TOOLS_WHERE} "
            f"AND supports_tools = FALSE"
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    # Exact revert: every gpt-5* row had supports_tools=false before
    # 074 (seeded by 070 and discovery), so a blanket restore on the
    # same predicate is precise. This is the lever if the auto-router
    # shift proves wrong in production.
    conn.execute(
        sa.text(
            f"UPDATE model_configs SET supports_tools = FALSE, "
            f"updated_at = NOW() WHERE {_TOOLS_WHERE}"
        )
    )
