"""Add search_providers table + tenant search config.

Web-search feature. Mirrors the LLM provider-key pattern: per-tenant
search provider rows with an AES-256-GCM-encrypted API key (stored
Text, same as provider_keys.api_key_encrypted), a priority-ordered
fallback chain, health tracking, and usage stats. Plus two tenant-level
columns: a search on/off toggle and a flat per-query credit cost.

Schema notes (adapted to the real gateway schema, NOT the idealized
spec): tenant_id is VARCHAR(36) referencing tenants.tenant_id (string
ids like "acme-corp-001"), and the encrypted key is Text — both match
the existing api_keys / provider_keys conventions.

Revision ID: 055
Revises: 054
Create Date: 2026-06-15
"""

import sqlalchemy as sa
from alembic import op


revision = "055"
down_revision = "054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "search_providers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Provider identity
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=True),
        # AES-256-GCM-encrypted key (Text — same as provider_keys).
        sa.Column("api_key_encrypted", sa.Text, nullable=False),
        # Config
        sa.Column("priority", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column(
            "is_enabled",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "max_results", sa.Integer, nullable=False, server_default=sa.text("5")
        ),
        # Health tracking
        sa.Column(
            "health_status",
            sa.String(20),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("last_health_check", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_reason", sa.Text, nullable=True),
        sa.Column(
            "uptime_pct",
            sa.Numeric(5, 2),
            nullable=True,
            server_default=sa.text("100.0"),
        ),
        sa.Column("avg_latency_ms", sa.Integer, nullable=True),
        # Stats
        sa.Column(
            "total_queries", sa.Integer, nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "total_failures", sa.Integer, nullable=False, server_default=sa.text("0")
        ),
        # Timestamps
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
            "tenant_id", "provider", "priority", name="uq_search_provider_priority"
        ),
    )
    op.create_index(
        "idx_search_providers_tenant",
        "search_providers",
        ["tenant_id", "is_enabled", "priority"],
    )

    # Tenant-level search config.
    op.add_column(
        "tenants",
        sa.Column(
            "search_enabled",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "search_credit_cost",
            sa.Integer,
            nullable=False,
            server_default=sa.text("5"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tenants", "search_credit_cost")
    op.drop_column("tenants", "search_enabled")
    op.drop_index("idx_search_providers_tenant", table_name="search_providers")
    op.drop_table("search_providers")
