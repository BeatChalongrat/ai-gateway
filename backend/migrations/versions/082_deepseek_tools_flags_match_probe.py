"""FIX51 Part 2.3 (OPEN_DECISIONS #47) — DeepSeek v4 tools flags match reality.

``deepseek:deepseek-v4-pro`` and ``deepseek:deepseek-v4-flash`` were
seeded manually with ``supports_tools = false``. The FIX47.6 Part B
capability probe DROVE both models with a real tool schema three times
each and every run came back ``outcome: tool_call_emitted`` /
``observed_supports_tools: true``:

    the capability-flags probe's three runs of 2026-09-02 (21:00:15Z, 21:02:23Z,
    21:07:14Z), kept with the probes outside the repository.

This flips the two flags to what the probe observed — evidence, not the
vendor's documentation. ``openrouter:deepseek/deepseek-r1`` also emitted
tool calls in 2 of 3 runs but stays untouched here: 2/3 is not the 3/3
bar, and reasoning-model tool turns were a live trap in FIX42.

Guarded UPDATE — a deployment without these manual rows is a no-op.
"""

from alembic import op

revision = "082"
down_revision = "081"
branch_labels = None
depends_on = None

_MODELS = ("deepseek-v4-pro", "deepseek-v4-flash")


def upgrade() -> None:
    op.execute(
        "UPDATE model_configs SET supports_tools = TRUE, "
        "is_manual_override = TRUE, updated_at = now() "
        "WHERE provider = 'deepseek' "
        f"AND model IN {_MODELS!r}"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE model_configs SET supports_tools = FALSE, updated_at = now() "
        "WHERE provider = 'deepseek' "
        f"AND model IN {_MODELS!r}"
    )
