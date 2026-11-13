"""Current image default and retirement of obsolete video catalogue entries.

Only replaces the exact legacy seeded image order; custom orders remain intact.
Does not activate an image model for chat routing or create provider credentials.
Revision ID: 090; Revises: 089
"""
from alembic import op

revision = "090"
down_revision = "089"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""INSERT INTO model_configs
        (provider, model, supports_image_gen, is_active, show_in_chat, cost_per_input_token, source)
        VALUES ('openai', 'gpt-image-2.6-flare', TRUE, FALSE, FALSE, 0.000008, 'seed')
        ON CONFLICT (provider, model) DO NOTHING""")
    op.execute("""UPDATE system_settings SET value='openai:gpt-image-2.6-flare'
        WHERE key='media_order:image' AND value='openai:gpt-image-1'""")
    op.execute("""UPDATE model_configs SET supports_image_gen=FALSE
        WHERE provider='openai' AND model='gpt-image-1'""")
    op.execute("""UPDATE model_configs SET is_active=FALSE, show_in_chat=FALSE, supports_video_gen=FALSE
        WHERE provider='openai' AND model LIKE 'sora-%'""")


def downgrade():
    # Preserve model/history rows and do not reactivate retired services.
    op.execute("""UPDATE system_settings SET value='' WHERE key='media_order:image'
        AND value='openai:gpt-image-2.6-flare'""")
