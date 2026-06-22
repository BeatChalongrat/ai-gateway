"""Seed audio + TTS models (Sprint 4).

Three dev-only ``model_configs`` rows so the new adapters have models to
dispatch against once a provider key is configured:

  - Replicate MusicGen  (music, supports_audio_gen, ~$0.0002/s, model meta/musicgen)
  - OpenAI gpt-4o-mini-tts (tts, supports_tts, ~$0.000015/char)
  - ElevenLabs multilingual v2 (tts, supports_tts, ~$0.0001/char)

Uses the REAL columns (provider, model, supports_audio_gen, supports_tts,
cost_per_second, cost_per_character, source, is_active) — there is no
``model_id_provider``/``capability_metadata`` column; the provider path IS the
``model`` value (e.g. ``meta/musicgen``), which the adapter submits against.

Idempotent (``ON CONFLICT (provider, model) DO NOTHING``) and dev-only
(skipped when ``APP_ENV=production``), like migrations 057/059. Pricing is
UNVERIFIED against live provider pages (this sprint makes no live calls) —
confirm during the live smoke and adjust if it differs.

Revision ID: 060
Revises: 059
Create Date: 2026-06-23
"""

import os

from alembic import op


revision = "060"
down_revision = "059"
branch_labels = None
depends_on = None

_SEEDED = [
    ("replicate", "meta/musicgen"),
    ("openai", "gpt-4o-mini-tts"),
    ("elevenlabs", "eleven_multilingual_v2"),
]


def upgrade() -> None:
    if os.environ.get("APP_ENV", "development").lower() == "production":
        return
    op.execute(
        """
        INSERT INTO model_configs
            (provider, model, supports_audio_gen, supports_tts,
             cost_per_second, cost_per_character, source, is_active)
        VALUES
            ('replicate', 'meta/musicgen',          true,  false, 0.000200, NULL,     'seed', true),
            ('openai',    'gpt-4o-mini-tts',         false, true,  NULL,     0.000015, 'seed', true),
            ('elevenlabs','eleven_multilingual_v2',  false, true,  NULL,     0.000100, 'seed', true)
        ON CONFLICT (provider, model) DO NOTHING
        """
    )


def downgrade() -> None:
    # Delete ONLY the rows this migration seeded (other openai/replicate rows
    # must survive).
    op.execute(
        """
        DELETE FROM model_configs
        WHERE (provider, model) IN (
            ('replicate', 'meta/musicgen'),
            ('openai', 'gpt-4o-mini-tts'),
            ('elevenlabs', 'eleven_multilingual_v2')
        )
        """
    )
