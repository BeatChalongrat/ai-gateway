"""Seed real Replicate video models (Sprint 2B).

Two dev-only model_configs rows so the Replicate adapter has models to
submit against once a token is configured. Uses the REAL columns (provider,
model, supports_video_gen, cost_per_second, source, is_active) — there is no
``display_name``/``model_id_provider``/``capability_metadata`` column. The
Replicate path (``owner/name``) IS the ``model`` value; the adapter POSTs to
``/v1/models/{model}/predictions``.

Idempotent (``ON CONFLICT (provider, model) DO NOTHING``) and dev-only
(skipped when ``APP_ENV=production``), like migration 057.

Pricing (~$0.07/s Kling, ~$0.05/s Wan) is from Audit v2 and is UNVERIFIED
against the live Replicate pages (this sprint makes no live calls) — confirm
during the live smoke and adjust if Replicate's current price differs.

Revision ID: 059
Revises: 058
Create Date: 2026-06-22
"""

import os

from alembic import op


revision = "059"
down_revision = "058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if os.environ.get("APP_ENV", "development").lower() == "production":
        return
    op.execute(
        """
        INSERT INTO model_configs
            (provider, model, supports_video_gen, cost_per_second, source, is_active)
        VALUES
            ('replicate', 'kwaivgi/kling-v1.5-pro', true, 0.070000, 'seed', true),
            ('replicate', 'wan-video/wan-2.5',      true, 0.050000, 'seed', true)
        ON CONFLICT (provider, model) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM model_configs WHERE provider = 'replicate'")
