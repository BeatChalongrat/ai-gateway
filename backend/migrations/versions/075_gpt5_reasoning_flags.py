"""Flip gpt-5 reasoning flags — the follow-up 074 deferred.

074 corrected ``supports_tools`` but deliberately left
``supports_reasoning=false`` because, at the time, flipping it made the
adapter inject ``reasoning_effort`` on /chat/completions where the
whole gpt-5 generation rejects effort+tools (400). That blocker is now
closed: the adapter routes gpt-5 reasoning turns — tools included —
through /v1/responses (the endpoint OpenAI's own 400 prescribes), with
a chat/completions fallback that pins effort to 'none'. Live-verified
2026-08-17: tool call + reasoning summary text arrive together, and
the stateless tool-result echo is accepted.

Flips ``supports_reasoning=true`` + ``reasoning_strategy=
'openai_responses_reasoning'`` for active openai ``gpt-5*`` rows
EXCEPT the ``*chat-latest`` aliases — those are the chat-tuned
non-reasoning variants; advertising reasoning on them would auto-enable
effort/Responses on a model tuned to answer without it.

Effects to QA (see the MR checklist):
* the orchestrator auto-enables mode=auto + show_summary for these
  rows when the caller sends no reasoning config — so most gpt-5
  traffic (both clients) moves to /v1/responses;
* the models endpoint starts advertising ``reasoning`` on these rows,
  which makes the clients' Thinking toggle visible where their own
  catalogs allow;
* reasoning-hinted auto routing can now elect budget gpt-5 rows where
  previously only the o-series qualified.

Revision ID: 075
Revises: 074
Create Date: 2026-08-17
"""

import sqlalchemy as sa
from alembic import op


revision = "075"
down_revision = "074"
branch_labels = None
depends_on = None

_WHERE = (
    "provider = 'openai' AND model LIKE 'gpt-5%' "
    "AND model NOT LIKE '%chat-latest%'"
)


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            f"UPDATE model_configs SET supports_reasoning = TRUE, "
            f"reasoning_strategy = 'openai_responses_reasoning', "
            f"updated_at = NOW() WHERE {_WHERE} "
            f"AND supports_reasoning = FALSE"
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    # Exact revert: every row on this predicate carried
    # supports_reasoning=false / reasoning_strategy='none' before 075
    # (074 touched only supports_tools). This is the lever if the
    # Responses-path shift misbehaves in production — flipping back
    # returns all gpt-5 traffic to plain /chat/completions.
    conn.execute(
        sa.text(
            f"UPDATE model_configs SET supports_reasoning = FALSE, "
            f"reasoning_strategy = 'none', updated_at = NOW() "
            f"WHERE {_WHERE}"
        )
    )
