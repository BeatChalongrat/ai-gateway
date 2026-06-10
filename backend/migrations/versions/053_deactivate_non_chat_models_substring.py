"""Deactivate non-chat models — comprehensive substring sweep (follow-up to 052).

052 used prefix LIKE for image models and missed substring cases:
``chatgpt-image-latest`` (not ``gpt-image%``), plus the ``gpt-audio*`` /
``gpt-realtime*`` (voice API) families and ``mistral-embed`` that aren't
text-chat models but slipped past the original keyword set. This migration
deactivates them with SUBSTRING patterns so the chat catalog (and the auto-
routing candidate pool) only contains real chat models.

``provider_discovery._NON_CHAT_KEYWORDS`` was extended with ``audio`` /
``realtime`` (and already had ``gpt-image`` which matches ``chatgpt-image``),
so a fresh DB never adds these and the sync soft-deactivates them; this
migration cleans an existing deploy immediately.

Only AUTO-sourced, non-manual rows are touched (``is_manual_override=false``).
Idempotent (re-running, or running over 052's already-deactivated rows, is a
no-op). Downgrade re-activates the matched rows.

Revision ID: 053
Revises: 052
Create Date: 2026-06-10
"""

from alembic import op


revision = "053"
down_revision = "052"
branch_labels = None
depends_on = None

# Substring patterns. ``turbo-instruct`` (not bare ``instruct``) keeps
# instruction-tuned CHAT models. ``davinci``/``babbage`` are prefix-anchored
# legacy base models. ``%gpt-image%`` also catches ``chatgpt-image-latest``.
_WHERE = """
    is_manual_override = false
    AND (
        model LIKE '%transcribe%'
        OR model LIKE '%turbo-instruct%'
        OR model LIKE '%gpt-image%'
        OR model LIKE '%dall-e%'
        OR model LIKE '%audio%'
        OR model LIKE '%realtime%'
        OR model LIKE '%embed%'
        OR model LIKE 'davinci%'
        OR model LIKE 'babbage%'
    )
"""


def upgrade() -> None:
    op.execute(
        f"UPDATE model_configs SET is_active = false, updated_at = NOW() "
        f"WHERE is_active = true AND ({_WHERE})"
    )


def downgrade() -> None:
    op.execute(
        f"UPDATE model_configs SET is_active = true, updated_at = NOW() "
        f"WHERE ({_WHERE})"
    )
