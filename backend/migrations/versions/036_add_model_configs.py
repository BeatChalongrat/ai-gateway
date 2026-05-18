"""Add ``model_configs`` table — DB-backed model registry.

Replaces the hardcoded ``_CANDIDATES`` list in
``app/services/providers/model_registry.py``. Capability + cost fields
mirror the existing ``ModelCandidate`` dataclass. Discovery metadata
columns (``source``, ``is_manual_override``, ``is_active``,
``last_synced_at``) let the background sync job upsert without
overwriting admin-edited rows and soft-deactivate models that have
disappeared from upstream APIs.

Seed migration ``037`` populates this table with the 48 current
candidates so the registry has data before the runtime singleton is
rebuilt against the DB.

Revision ID: 036
Revises: 035
Create Date: 2026-05-12
"""

from alembic import op
import sqlalchemy as sa


revision = "036"
down_revision = "035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        # --- Capability metadata ---
        sa.Column(
            "supports_streaming",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "supports_tools",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "supports_vision",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "max_context_tokens",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("128000"),
        ),
        # --- Cost / latency ---
        sa.Column(
            "cost_tier",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'standard'"),
        ),
        sa.Column(
            "latency_tier",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'standard'"),
        ),
        sa.Column(
            "cost_per_input_token",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0.0"),
        ),
        sa.Column(
            "cost_per_output_token",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0.0"),
        ),
        # --- Discovery metadata ---
        # ``source`` is one of {"auto", "manual", "seed"}.
        # "seed" rows are loaded once by migration 037 and otherwise
        # behave identically to "auto" rows for the sync logic.
        sa.Column(
            "source",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'auto'"),
        ),
        # When True the discovery sync MUST NOT overwrite this row —
        # an admin override is preserved across syncs.
        sa.Column(
            "is_manual_override",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        # Soft-delete flag flipped to False when an upstream API stops
        # listing the model. Routing reads only ``is_active=True`` rows.
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "last_synced_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "provider", "model",
            name="uq_model_configs_provider_model",
        ),
    )
    op.create_index(
        "ix_model_configs_provider",
        "model_configs",
        ["provider"],
    )
    op.create_index(
        "ix_model_configs_is_active",
        "model_configs",
        ["is_active"],
    )


def downgrade() -> None:
    op.drop_index("ix_model_configs_is_active", table_name="model_configs")
    op.drop_index("ix_model_configs_provider", table_name="model_configs")
    op.drop_table("model_configs")
