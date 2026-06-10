"""Deactivate non-chat models that auto-sync leaked into the chat catalog.

Audio transcription (gpt-4o-transcribe*), legacy completion/base models
(gpt-3.5-turbo-instruct*, davinci-002, babbage-002), and dedicated image
models (gpt-image-*) were auto-discovered into ``model_configs`` with
``is_active=true``, so they appeared in the chat model selector even though
they can't be used for chat (a user picking "gpt-4o-transcribe" gets a model
that doesn't chat).

``provider_discovery`` now infers ``is_chat=false`` for these (extended
``_NON_CHAT_KEYWORDS``), so a fresh DB never adds them and the discovery sync
soft-deactivates them. This migration deactivates the rows an existing deploy
already accumulated, so the catalog is clean immediately + reproducibly.

Only AUTO-sourced, non-manual rows are touched (``is_manual_override=false``)
so curated/manual catalog entries are never affected. ``turbo-instruct``
(not bare ``instruct``) so instruction-tuned CHAT models (…-instruct) survive.

Idempotent. Downgrade re-activates the matched rows.

Revision ID: 052
Revises: 051
Create Date: 2026-06-10
"""

from alembic import op


revision = "052"
down_revision = "051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE model_configs
        SET is_active = false, updated_at = NOW()
        WHERE is_active = true
          AND is_manual_override = false
          AND (
            model LIKE '%transcribe%'
            OR model LIKE '%turbo-instruct%'
            OR model LIKE 'davinci%'
            OR model LIKE 'babbage%'
            OR model LIKE 'gpt-image%'
          )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE model_configs
        SET is_active = true, updated_at = NOW()
        WHERE is_manual_override = false
          AND (
            model LIKE '%transcribe%'
            OR model LIKE '%turbo-instruct%'
            OR model LIKE 'davinci%'
            OR model LIKE 'babbage%'
            OR model LIKE 'gpt-image%'
          )
        """
    )
