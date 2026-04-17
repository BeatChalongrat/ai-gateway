"""Add policy_profiles and org_settings tables for Stage 7 admin UI.

Revision ID: 015
Revises: 014
Create Date: 2026-04-10
"""

from alembic import op
import sqlalchemy as sa


revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "policy_profiles",
        sa.Column("profile_id", sa.String(100), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "category_actions_json",
            sa.Text(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.create_table(
        "org_settings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "ner_enabled",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("org_settings")
    op.drop_table("policy_profiles")
