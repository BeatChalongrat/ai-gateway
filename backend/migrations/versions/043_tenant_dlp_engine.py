"""Add per-tenant DLP engine override.

Revision ID: 043
Revises: 042
Create Date: 2026-05-18

Phase-3c Rust DLP sidecar shipping. The global ``DLP_ENGINE`` env
var selects the default engine (``python`` / ``rust`` / ``shadow``);
operators with mixed-trust tenant pools need a per-tenant escape
hatch so a single tenant can be flipped to ``rust`` while the rest
of the fleet stays on ``python`` (or vice versa).

The new column is non-nullable with default ``inherit`` so every
existing row keeps the current global behaviour after migration.
The orchestrator's ``_select_dlp_engine(ctx)`` reads the column and
falls back to ``settings.dlp_engine`` when the value is ``inherit``.

### Downgrade

Drops the column. No data loss in either direction — ``inherit``
rows mean the system was relying on the global anyway, and a
downgrade simply re-pins everyone to the global with no behaviour
change.
"""

from alembic import op
import sqlalchemy as sa

revision = "043"
down_revision = "042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "dlp_engine",
            sa.String(length=20),
            nullable=False,
            server_default="inherit",
        ),
    )


def downgrade() -> None:
    op.drop_column("tenants", "dlp_engine")
