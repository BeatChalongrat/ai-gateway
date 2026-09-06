"""FIX72 — sranai qwen3.8-27b-fp8 honours the vLLM enable_thinking off-switch.

The row was seeded ``openai_compatible_reasoning_content`` (the box emits
``reasoning_content`` on its own; nothing is sent upstream, thinking always
on). FIX72 probed the actual endpoint (gateway.9arm.co, LiteLLM in front of
vLLM) DIRECTLY and measured that it accepts
``chat_template_kwargs={"enable_thinking": false}`` and that the switch
really works — non-stream completion dropped 59->31 tokens with tools,
streaming reasoning dropped 125->0 chars, and the tool call still fired;
every other switch (/no_think, reasoning_effort=none, top-level
enable_thinking, and litellm/effort/passthrough styles) parsed but did NOT stop
thinking. The new strategy ``openai_compatible_enable_thinking`` records that
this box takes the switch, so the adapter can honour ``reasoning.mode="off"``
for it while staying byte-identical on every other mode.

Guarded UPDATE — a deployment without this manual row is a no-op. Downgrade
restores the parse-only strategy (thinking stays on, the pre-FIX72 state).
"""

from alembic import op

revision = "086"
down_revision = "085"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE model_configs "
        "SET reasoning_strategy = 'openai_compatible_enable_thinking', "
        "is_manual_override = TRUE, updated_at = now() "
        "WHERE provider = 'sranai' AND model = 'qwen3.8-27b-fp8' "
        "AND supports_reasoning = TRUE"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE model_configs "
        "SET reasoning_strategy = 'openai_compatible_reasoning_content', "
        "updated_at = now() "
        "WHERE provider = 'sranai' AND model = 'qwen3.8-27b-fp8'"
    )
