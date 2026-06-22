"""Seed the dev-only mock video model (Sprint 2A).

Inserts the ``(provider='mock', model='mock-video-1')`` row so the video
generation vertical has a zero-cost model to submit against in dev/CI.

Schema note (same as migration 055/056): ``model_configs`` has no
``display_name`` and ``id`` is an auto Integer — the row is keyed by the
``UNIQUE(provider, model)`` business key. Every other column has a DB
default, so only provider/model/supports_video_gen/cost_per_second/source
are specified.

Dev-only: skipped when ``APP_ENV=production`` (the mock model must never
appear in a production catalog). Idempotent via ``ON CONFLICT DO NOTHING``.

Revision ID: 057
Revises: 056
Create Date: 2026-06-22
"""

import os

from alembic import op


revision = "057"
down_revision = "056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if os.environ.get("APP_ENV", "development").lower() == "production":
        # Production catalogs never carry the dev mock model. The migration
        # still stamps as applied; it simply seeds nothing.
        return
    op.execute(
        """
        INSERT INTO model_configs
            (provider, model, supports_video_gen, cost_per_second, source, is_active)
        VALUES
            ('mock', 'mock-video-1', true, 0.070000, 'seed', true)
        ON CONFLICT (provider, model) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM model_configs WHERE provider = 'mock' AND model = 'mock-video-1'"
    )
