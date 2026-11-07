"""FIX28 §5.2 — sora-2 / sora-2-pro are video models, not chat models.

They were catalogued with every capability flag false, so chat-edge derived
``model_kind=chat`` and they surfaced in the chat picker (selecting one fails
instead of producing a video) while the desktop's "Video" section said "No
models available". Flag them ``supports_video_gen`` like the other video rows.

Gate interaction checked before writing this: ``/v1/video/config`` reads
``enabled = gate and key_configured and model_verified and model_count > 0``.
This changes only ``model_count`` (and only when the gate is on); the other
three stay closed on this deployment (no VIDEO_GEN gate, no Replicate key, no
VIDEO_MODEL_VERIFIED), so video does not become enabled as a side effect.

Revision ID: 078
Revises: 077
Create Date: 2026-08-27
"""
import sqlalchemy as sa
from alembic import op

revision = "078"
down_revision = "077"
branch_labels = None
depends_on = None

_WHERE = "provider = 'openai' AND (model = 'sora-2' OR model = 'sora-2-pro')"


def upgrade() -> None:
    op.get_bind().execute(sa.text(
        f"UPDATE model_configs SET supports_video_gen = TRUE, updated_at = NOW() WHERE {_WHERE}"
    ))


def downgrade() -> None:
    op.get_bind().execute(sa.text(
        f"UPDATE model_configs SET supports_video_gen = FALSE, updated_at = NOW() WHERE {_WHERE}"
    ))
