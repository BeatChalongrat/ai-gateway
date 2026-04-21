"""Portal users, credit transactions, token packs; link api_keys/projects.

Introduces the self-service User Portal schema:

- ``portal_users``: end users who sign up via /portal/auth/signup. Each
  user owns a credit balance (tokens) and a plan tier.
- ``credit_transactions``: append-only ledger of every balance change
  (signup_bonus, usage, manual_topup, purchase, adjustment). Balance
  snapshot stored per row so audits never have to replay the ledger.
- ``token_packs``: admin-managed SKUs shown on /portal/billing. Seeded
  with Starter / Pro / Business defaults; payment backend (Stripe) is
  intentionally deferred — ``stripe_price_id`` left nullable.
- ``portal_user_id`` columns on ``api_keys`` and ``projects`` so the
  portal can filter to "my resources" and so the gateway can find the
  owning user for credit deduction.

UUIDs are stored as String(36) to stay portable with the SQLite test
engine (see migration 023 for the same convention).

Revision ID: 025
Revises: 024
Create Date: 2026-04-19
"""

from alembic import op
import sqlalchemy as sa


revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portal_users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column(
            "credit_balance",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "credit_used",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "plan",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'free_trial'"),
        ),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column(
            "email_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
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
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_portal_users_email", "portal_users", ["email"], unique=True
    )

    op.create_table(
        "credit_transactions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("portal_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("balance_after", sa.BigInteger(), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_credit_tx_user_created",
        "credit_transactions",
        ["user_id", "created_at"],
    )

    op.create_table(
        "token_packs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("tokens", sa.BigInteger(), nullable=False),
        sa.Column("price_usd", sa.Numeric(10, 2), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("stripe_price_id", sa.Text(), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "sort_order",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # Seed default token packs. IDs are fixed so re-running against a
    # fresh DB produces identical rows — admins can still delete/add via
    # the admin API without conflicting with these.
    op.bulk_insert(
        sa.table(
            "token_packs",
            sa.column("id", sa.String(36)),
            sa.column("name", sa.Text()),
            sa.column("tokens", sa.BigInteger()),
            sa.column("price_usd", sa.Numeric(10, 2)),
            sa.column("description", sa.Text()),
            sa.column("sort_order", sa.Integer()),
        ),
        [
            {
                "id": "pack-starter-000",
                "name": "Starter",
                "tokens": 1_000_000,
                "price_usd": 10.00,
                "description": "1M tokens — good for small projects",
                "sort_order": 1,
            },
            {
                "id": "pack-pro-000",
                "name": "Pro",
                "tokens": 5_000_000,
                "price_usd": 40.00,
                "description": "5M tokens — most popular",
                "sort_order": 2,
            },
            {
                "id": "pack-business-000",
                "name": "Business",
                "tokens": 20_000_000,
                "price_usd": 120.00,
                "description": "20M tokens — for high-volume usage",
                "sort_order": 3,
            },
        ],
    )

    # Link resources to portal owners. Both columns are nullable so
    # existing admin/tenant-scoped keys and projects keep working —
    # portal-owned rows simply have this set.
    #
    # Use batch_alter_table so the same migration runs against SQLite
    # (test engine) and Postgres. SQLite's ALTER TABLE can't add FK
    # constraints — batch mode copies the table and recreates it with
    # the constraint in place.
    # Batch mode requires named constraints.
    with op.batch_alter_table("api_keys") as batch:
        batch.add_column(
            sa.Column(
                "portal_user_id",
                sa.String(36),
                sa.ForeignKey(
                    "portal_users.id",
                    ondelete="SET NULL",
                    name="fk_api_keys_portal_user_id",
                ),
                nullable=True,
            )
        )
    op.create_index(
        "idx_api_keys_portal_user", "api_keys", ["portal_user_id"]
    )

    with op.batch_alter_table("projects") as batch:
        batch.add_column(
            sa.Column(
                "portal_user_id",
                sa.String(36),
                sa.ForeignKey(
                    "portal_users.id",
                    ondelete="SET NULL",
                    name="fk_projects_portal_user_id",
                ),
                nullable=True,
            )
        )
    op.create_index(
        "idx_projects_portal_user", "projects", ["portal_user_id"]
    )


def downgrade() -> None:
    op.drop_index("idx_projects_portal_user", table_name="projects")
    op.drop_column("projects", "portal_user_id")

    op.drop_index("idx_api_keys_portal_user", table_name="api_keys")
    op.drop_column("api_keys", "portal_user_id")

    op.drop_table("token_packs")

    op.drop_index("idx_credit_tx_user_created", table_name="credit_transactions")
    op.drop_table("credit_transactions")

    op.drop_index("idx_portal_users_email", table_name="portal_users")
    op.drop_table("portal_users")
