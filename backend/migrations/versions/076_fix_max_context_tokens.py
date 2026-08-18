"""Fix rows carrying a max-OUTPUT value in ``max_context_tokens``.

Root cause: the discovery pricing enrichment preferred litellm's
``max_tokens`` key over ``max_input_tokens`` — for many litellm
entries ``max_tokens`` is the max *output* figure, so rows synced from
that path recorded 4096/16384/32768/100000 as their "context window"
(gpt-4o-mini said 16384; its real context is 128K). The enrichment
order is fixed in the same change-set; this migration repairs the
data already written.

Corrects 32 active rows to the provider's published context window:

  4096   -> 200000   claude-3-haiku-20240307, claude-3-opus-20240229
  4096   -> 16385    gpt-3.5-turbo{,-0125,-1106,-16k}
  4096   -> 8192     gpt-4, gpt-4-0613
  4096   -> 128000   gpt-4o-2024-05-13, gpt-4-turbo{,-2024-04-09}
  16384  -> 128000   gpt-4o{,-2024-08-06,-2024-11-20},
                     gpt-4o-mini{,-2024-07-18},
                     gpt-5-chat-latest, gpt-5.1-chat-latest
  32768  -> 1047576  gpt-4.1{,-2025-04-14}, gpt-4.1-mini{,-…},
                     gpt-4.1-nano{,-…}
  100000 -> 200000   o1{,-2024-12-17}, o1-pro{,-2025-03-19},
                     o3{,-2025-04-16}, o3-mini-2025-01-31,
                     o4-mini-2025-04-16

Deliberately NOT corrected:
* ``gpt-5.2-chat-latest`` / ``gpt-5.3-chat-latest`` — no published
  context window could be confirmed for these; guessing here is the
  same class of error this migration removes.
* every inactive row (transcribe/audio/realtime/babbage/davinci/
  instruct) — deactivated non-chat rows are served nowhere; touching
  them is churn without user value.

Idempotent both ways: every UPDATE is conditioned on the exact value
it replaces, so re-running either direction is a no-op, and rows an
admin has since hand-edited are left alone. Downgrade restores the
exact pre-migration values (wrong, but faithfully wrong) — it exists
so a rollback of the chat-edge context flip can restore the previous
end-to-end state.

Revision ID: 076
Revises: 075
Create Date: 2026-08-18
"""

import sqlalchemy as sa
from alembic import op


revision = "076"
down_revision = "075"
branch_labels = None
depends_on = None


# (provider, model, wrong_value, correct_value)
_FIXES: list[tuple[str, str, int, int]] = [
    ("anthropic", "claude-3-haiku-20240307", 4096, 200000),
    ("anthropic", "claude-3-opus-20240229", 4096, 200000),
    ("openai", "gpt-3.5-turbo", 4096, 16385),
    ("openai", "gpt-3.5-turbo-0125", 4096, 16385),
    ("openai", "gpt-3.5-turbo-1106", 4096, 16385),
    ("openai", "gpt-3.5-turbo-16k", 4096, 16385),
    ("openai", "gpt-4", 4096, 8192),
    ("openai", "gpt-4-0613", 4096, 8192),
    ("openai", "gpt-4o-2024-05-13", 4096, 128000),
    ("openai", "gpt-4-turbo", 4096, 128000),
    ("openai", "gpt-4-turbo-2024-04-09", 4096, 128000),
    ("openai", "gpt-4o", 16384, 128000),
    ("openai", "gpt-4o-2024-08-06", 16384, 128000),
    ("openai", "gpt-4o-2024-11-20", 16384, 128000),
    ("openai", "gpt-4o-mini", 16384, 128000),
    ("openai", "gpt-4o-mini-2024-07-18", 16384, 128000),
    ("openai", "gpt-5-chat-latest", 16384, 128000),
    ("openai", "gpt-5.1-chat-latest", 16384, 128000),
    ("openai", "gpt-4.1", 32768, 1047576),
    ("openai", "gpt-4.1-2025-04-14", 32768, 1047576),
    ("openai", "gpt-4.1-mini", 32768, 1047576),
    ("openai", "gpt-4.1-mini-2025-04-14", 32768, 1047576),
    ("openai", "gpt-4.1-nano", 32768, 1047576),
    ("openai", "gpt-4.1-nano-2025-04-14", 32768, 1047576),
    ("openai", "o1", 100000, 200000),
    ("openai", "o1-2024-12-17", 100000, 200000),
    ("openai", "o1-pro", 100000, 200000),
    ("openai", "o1-pro-2025-03-19", 100000, 200000),
    ("openai", "o3", 100000, 200000),
    ("openai", "o3-2025-04-16", 100000, 200000),
    ("openai", "o3-mini-2025-01-31", 100000, 200000),
    ("openai", "o4-mini-2025-04-16", 100000, 200000),
]

_STMT = sa.text(
    "UPDATE model_configs SET max_context_tokens = :to_val, "
    "updated_at = NOW() "
    "WHERE provider = :provider AND model = :model "
    "AND max_context_tokens = :from_val"
)


def upgrade() -> None:
    conn = op.get_bind()
    for provider, model, wrong, correct in _FIXES:
        conn.execute(
            _STMT,
            {
                "provider": provider,
                "model": model,
                "from_val": wrong,
                "to_val": correct,
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    for provider, model, wrong, correct in _FIXES:
        conn.execute(
            _STMT,
            {
                "provider": provider,
                "model": model,
                "from_val": correct,
                "to_val": wrong,
            },
        )
