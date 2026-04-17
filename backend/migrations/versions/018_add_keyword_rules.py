"""Add keyword_rules table and router_used column on request_logs.

- keyword_rules stores DB-configurable keyword → model hints with
  tenant/project scope.
- request_logs.router_used records which router won for each request
  (semantic / keyword / service) so /admin/routing/stats can break
  down routing decisions.

Revision ID: 018
Revises: 017
Create Date: 2026-04-11
"""

from alembic import op
import sqlalchemy as sa


revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "keyword_rules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("keywords_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("model_override", sa.String(100), nullable=False),
        sa.Column("priority", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("scope", sa.String(20), nullable=False, server_default="global"),
        sa.Column("scope_id", sa.String(36), nullable=True),
        sa.Column(
            "enabled",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_keyword_rules_scope",
        "keyword_rules",
        ["scope", "scope_id", "enabled"],
    )

    op.add_column(
        "request_logs",
        sa.Column("router_used", sa.String(20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("request_logs", "router_used")
    op.drop_index("ix_keyword_rules_scope", table_name="keyword_rules")
    op.drop_table("keyword_rules")
