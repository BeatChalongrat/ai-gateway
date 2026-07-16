"""Per-tenant raw-egress policy for the embeddings + voice channels
(FIX 2 — closing the chat-edge → OpenAI DLP bypass).

The gateway now fronts embeddings (KB indexing + RAG query) and voice
(TTS + STT), bringing them under the same intentional-raw-egress regime
as web search / media generation (migration 064). These two columns are
the per-channel equivalents of ``search_pii_egress`` / ``media_pii_egress``:

``kb_pii_egress`` / ``voice_pii_egress``: ``allow`` (default — today's
behavior: identity PII may egress raw to OpenAI for embeddings/voice,
audited) or ``block`` (fail closed on ANY identity PII for
compliance-strict tenants; for STT, refuse the un-scannable audio). The
credential/secret class always blocks regardless of these.

Revision ID: 065
Revises: 064
Create Date: 2026-07-16
"""

import sqlalchemy as sa
from alembic import op


revision = "065"
down_revision = "064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "kb_pii_egress",
            sa.String(8),
            nullable=False,
            server_default="allow",
        ),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "voice_pii_egress",
            sa.String(8),
            nullable=False,
            server_default="allow",
        ),
    )


def downgrade() -> None:
    op.drop_column("tenants", "voice_pii_egress")
    op.drop_column("tenants", "kb_pii_egress")
