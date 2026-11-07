"""FIX67 — image models become catalogue rows; media destination order settings.

Part 1: give image the shape video already has. A ``supports_image_gen`` flag
(mirrors ``supports_video_gen``) marks the image rows, so ``/v1/models`` lists
them from the catalogue and a new image backend is a row + an adapter
registration + an order entry — no ``IMAGE_GENERATION_PROVIDERS`` frozenset to
edit.

Why the image rows stay ``is_active = FALSE``: an image model has per-token cost
0, so if it entered the routing candidate pool (which loads ``is_active`` rows)
the auto-router would elect it for a CHAT turn — the very reason images were kept
out of the pool. Keeping them inactive means they never reach ``get_all_candidates``,
so they can never ride the chat picker OR the router. They are listed by a
dedicated ``supports_image_gen`` query in ``public_models`` instead. ``show_in_chat``
is set false on them as belt-and-braces.

Part 2/3: seed the media destination order + the mock kill-switch.
  * ``media_order:image``  = ``openai:gpt-image-1`` (production default — one real
    backend, exactly today's behaviour, no mock);
  * ``media_order:video``  = `` `` (empty — no video fallback by default);
  * ``media_allow_mock``   = ``false`` (the mock provider is opt-in; a mock artifact
    handed to a real user unaware is worse than none — the mock-chat-1 incident).

The dev-only ``mock-image-1`` row is seeded like ``mock-video-1`` (057) and, like
it, is SKIPPED when ``APP_ENV=production`` so a production catalogue never carries
it. ``gpt-image-1`` production behaviour is unchanged.

Revision ID: 085
Revises: 084
Create Date: 2026-09-05
"""

import os

import sqlalchemy as sa
from alembic import op

revision = "085"
down_revision = "084"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # --- Part 1: the capability flag + flag the image rows ---------------
    op.add_column(
        "model_configs",
        sa.Column(
            "supports_image_gen",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    bind.execute(sa.text(
        "UPDATE model_configs SET supports_image_gen = TRUE, show_in_chat = FALSE, "
        "updated_at = NOW() "
        "WHERE provider = 'openai' AND model IN ('gpt-image-1', 'gpt-image-1-mini')"
    ))

    # dev-only mock image destination (skipped in production, like 057).
    if os.environ.get("APP_ENV", "development").lower() != "production":
        bind.execute(sa.text(
            """
            INSERT INTO model_configs
                (provider, model, supports_image_gen, is_active, show_in_chat,
                 cost_per_input_token, source)
            VALUES
                ('mock', 'mock-image-1', TRUE, FALSE, FALSE, 0.0, 'seed')
            ON CONFLICT (provider, model) DO NOTHING
            """
        ))

    # --- Part 2/3: media destination order + mock kill-switch -----------
    _seed_setting(
        bind, "media_order:image", "openai:gpt-image-1",
        "Ordered image destinations (provider:model, comma-separated). The first "
        "that produces wins; a failure falls to the next (max 2, never a costlier "
        "tier, cross-provider). No mock unless media_allow_mock is on.",
    )
    _seed_setting(
        bind, "media_order:video", "",
        "Ordered video destinations (provider:model, comma-separated). Empty = no "
        "fallback. Fallback happens only BEFORE the provider job is dispatched.",
    )
    _seed_setting(
        bind, "media_allow_mock", "false",
        "When true, the test-only 'mock' media provider may serve a request. "
        "Default false — a mock artifact must never reach a real user unaware.",
    )


def _seed_setting(bind, key: str, value: str, description: str) -> None:
    bind.execute(
        sa.text(
            "INSERT INTO system_settings (key, value, description) "
            "VALUES (:k, :v, :d) ON CONFLICT (key) DO NOTHING"
        ),
        {"k": key, "v": value, "d": description},
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(
        "DELETE FROM system_settings WHERE key IN "
        "('media_order:image', 'media_order:video', 'media_allow_mock')"
    ))
    bind.execute(sa.text(
        "DELETE FROM model_configs WHERE provider = 'mock' AND model = 'mock-image-1'"
    ))
    op.drop_column("model_configs", "supports_image_gen")
