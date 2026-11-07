"""FIX27 Part 2 — supports_vision flags corrected from a LIVE probe.

``model_configs.supports_vision`` is the single source of truth the
clients read (chat-edge /models appends "vision" from it; web-chat's
attach gate, the composer's attach-button label and the picker badge all
read that string; the desktop app renders the same page). It said FALSE
for every OpenAI model except the gpt-4o family, so the picker labelled
the default model (gpt-5.6-terra) "this model can't read images" and
dropped pasted images — while the same model reads images fine.

Probe (2026-08-27, through the gateway, one 900×400 PNG with printed
text, "what is the order code"): the code came back verbatim from
gpt-5.6-terra/luna/sol, gpt-5.5, gpt-5.4/-mini/-nano, gpt-5.2, gpt-5.1,
gpt-5/-mini/-nano, gpt-4.1/-mini/-nano, o1, o3, o4-mini, gpt-4-turbo.
NOT from o3-mini (empty answer, twice) — left false. NOT probed (left
false, say so if a user asks): the *-pro, *-codex and *-chat-latest
variants, and models of other providers.

Dated snapshots (``gpt-5-2025-08-07`` …) are the same models as their
bare ids and inherit the flag.

Revision ID: 077
Revises: 076
Create Date: 2026-08-27
"""
import sqlalchemy as sa
from alembic import op

revision = "077"
down_revision = "076"
branch_labels = None
depends_on = None

# bare ids verified live; each also covers its dated snapshots (id || '-20%')
_VERIFIED = [
    "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.6-sol",
    "gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano",
    "gpt-5.2", "gpt-5.1", "gpt-5", "gpt-5-mini", "gpt-5-nano",
    "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
    "o1", "o3", "o4-mini", "gpt-4-turbo",
]


def _where() -> str:
    ors = " OR ".join(
        f"model = '{m}' OR model LIKE '{m}-20%'" for m in _VERIFIED
    )
    return f"provider = 'openai' AND ({ors})"


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            f"UPDATE model_configs SET supports_vision = TRUE, updated_at = NOW() "
            f"WHERE {_where()} AND supports_vision = FALSE"
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            f"UPDATE model_configs SET supports_vision = FALSE, updated_at = NOW() "
            f"WHERE {_where()}"
        )
    )
