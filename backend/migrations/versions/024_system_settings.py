"""System settings key/value store.

Generic settings table used by the User Portal (free trial token grant,
signup toggle, credit low-balance threshold, etc.). Seeded with the four
defaults the portal reads on signup / dashboard render.

Revision ID: 024
Revises: 023
Create Date: 2026-04-19
"""

from alembic import op
import sqlalchemy as sa


revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_settings",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.bulk_insert(
        sa.table(
            "system_settings",
            sa.column("key", sa.Text()),
            sa.column("value", sa.Text()),
            sa.column("description", sa.Text()),
        ),
        [
            {
                "key": "free_trial_tokens",
                "value": "100000",
                "description": "Tokens granted to new portal users on signup",
            },
            {
                "key": "trial_model_tier",
                "value": "budget",
                "description": "Max model tier allowed for trial users: budget/standard/premium",
            },
            {
                "key": "portal_signup_enabled",
                "value": "true",
                "description": "Toggle to enable/disable new user registrations",
            },
            {
                "key": "credit_low_threshold",
                "value": "10000",
                "description": "Warn user when credit drops below this value",
            },
        ],
    )


def downgrade() -> None:
    op.drop_table("system_settings")
