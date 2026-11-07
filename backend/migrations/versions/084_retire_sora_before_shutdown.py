"""FIX66 Part 1 — retire Sora before OpenAI's shutdown, without resurrecting 078.

OpenAI's deprecations page (verified 2026-09-05 at
developers.openai.com/api/docs/deprecations) says: announced 2026-03-24,
**shutdown 2026-09-24** — the Videos API and every Sora 2 alias/snapshot
(``sora-2``, ``sora-2-pro``, and the dated snapshots) are removed from the
API that day.

State of Sora on this deployment BEFORE this migration (checked, not assumed):

  * ``openai/sora-2`` and ``openai/sora-2-pro`` are rows with
    ``supports_video_gen = TRUE`` (migration 078) and ``is_active = TRUE``.
  * There is **no OpenAI video adapter** registered — the media_gen registry
    holds only ``mock`` and ``replicate`` for job_type ``video`` (see
    ``media_gen/__init__.py``). A Sora job would fail ``no_adapter``.
  * Video generation is OFF deployment-wide (``video_gen_enabled`` false, no
    verified model), so nothing offers these today.

So Sora is already non-functional here; these rows are only future exposure:
if video were ever enabled they would be OFFERED and then fail — the same
"screen says available, provider 404s" trap as ``claude-3.5-haiku``.

The switch chosen — and WHY it is ``is_active``, not ``supports_video_gen``:

  * ``is_active = FALSE`` drops the rows from the media listing
    (``public_models.py`` filters ``is_active`` there) so no caller can pick
    them, and it is reversible from the models page / this downgrade.
  * We deliberately KEEP ``supports_video_gen = TRUE``. Setting it false would
    leave a row with every capability flag false, which chat-edge's
    ``deriveModelKind`` reads as a CHAT model — the exact regression migration
    078 fixed ("sora-2 rode the chat listing"). Retiring Sora must not
    recreate that bug, so the model KIND stays video and only the ACTIVE
    switch moves.

These are video rows, not chat rows, so this does not touch any chat model's
``is_active`` (a FIX66 tripwire). Total model_configs row count is unchanged.

Reversible: ``downgrade`` re-activates both rows. (Re-activation alone would
still not make Sora work — an OpenAI video adapter would also be needed — but
the switch itself is honestly reversible.)

Revision ID: 084
Revises: 083
Create Date: 2026-09-05
"""
import sqlalchemy as sa
from alembic import op

revision = "084"
down_revision = "083"
branch_labels = None
depends_on = None

_WHERE = "provider = 'openai' AND model IN ('sora-2', 'sora-2-pro')"


def upgrade() -> None:
    op.get_bind().execute(sa.text(
        f"UPDATE model_configs SET is_active = FALSE, updated_at = NOW() WHERE {_WHERE}"
    ))


def downgrade() -> None:
    op.get_bind().execute(sa.text(
        f"UPDATE model_configs SET is_active = TRUE, updated_at = NOW() WHERE {_WHERE}"
    ))
