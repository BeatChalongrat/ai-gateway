"""Add project-level routing config columns.

Revision ID: 044
Revises: 043
Create Date: 2026-05-19

May 2026 routing audit established that the routing engine reads
``allowed_providers``, ``preferred_provider``, ``blocked_models``, and
``default_model`` from ``payload.metadata`` only — and that nothing in
the gateway populates these from the project record. The ``projects``
table had no columns to store them, so per-project routing was
effectively unusable: every request had to pass the constraints
explicitly in its body, which most SDK callers will never do.

This migration adds the four columns. All nullable — ``NULL`` means
"defer to global defaults / caller-supplied metadata".

* ``default_model``       — string, e.g. ``"gpt-4o"`` for project's
                           preferred starting model when caller omits
                           the field.
* ``preferred_provider``  — string, e.g. ``"anthropic"`` — soft bias
                           in ``RoutingService._score_candidate``.
* ``allowed_providers``   — JSONB array of provider names, e.g.
                           ``["openai", "anthropic"]``. Empty / null =
                           no restriction.
* ``blocked_models``      — JSONB array of model names that must NOT
                           be routed to, regardless of caller intent.

### Downgrade

Drops the four columns. Any per-project routing config will be lost.
The routing engine continues to read from ``payload.metadata`` so the
runtime behaviour after downgrade is identical to the pre-044 state.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "044"
down_revision = "043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("default_model", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("preferred_provider", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column(
            "allowed_providers",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "blocked_models",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "blocked_models")
    op.drop_column("projects", "allowed_providers")
    op.drop_column("projects", "preferred_provider")
    op.drop_column("projects", "default_model")
