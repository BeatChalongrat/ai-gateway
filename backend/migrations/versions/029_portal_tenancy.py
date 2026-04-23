"""Phase 3 — Portal tenancy foundation (P0-4).

Replaces the Phase 2-0 duct-tape (migration 027) with real per-tenant
columns on ``portal_users`` and a ``tenant_invites`` table for the
owner-generated invite-code flow. Internal-tool scope per
``PORTAL_TENANCY_MIGRATION_PLAN.md``.

What this migration does (single transaction):

1. Ensures the ``demo-tenant-000`` row exists and renames it to
   "Default Team" (Option C from the design doc §4). The stable PK
   means every existing ``projects``/``api_keys``/``request_logs`` row
   stays valid with zero rewrites.
2. Adds three columns to ``portal_users``:
   - ``tenant_id`` (nullable here; flipped to NOT NULL by migration
     030 once the backfill in step 4 has populated every row).
   - ``tenant_role`` — ``'owner'`` | ``'member'``, server default
     ``'member'``.
   - ``token_version`` — integer, server default 1. Portal JWTs carry
     this claim and are rejected when they drift from the DB value;
     the bump in step 5 invalidates every pre-migration token.
3. Creates ``tenant_invites`` with a single-use opaque code, 7-day
   default expiry, and FK cascades scoped to the parent tenant.
4. Backfills every existing portal user into the renamed tenant and
   promotes the oldest one to ``owner``. If zero portal users exist,
   the first post-migration signup takes ownership — handled in
   application code, not here.
5. Bumps ``token_version`` for every existing row so pre-cutover JWTs
   fail verification on the next request (one-time re-login).

Idempotency is Alembic's job (via the ``alembic_version`` table);
this migration is not safe to run twice by hand, but a second
``alembic upgrade`` is a no-op by design.

Revision ID: 029
Revises: 028
Create Date: 2026-04-23
"""

from alembic import op
import sqlalchemy as sa


revision = "029"
down_revision = "028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------- Step 1: rename demo-tenant-000 to "Default Team" ----
    # UPDATE first; if the row is missing (fresh install where migration
    # 027 somehow didn't run) the INSERT ... WHERE NOT EXISTS below
    # creates it. Both statements are SQLite- and Postgres-compatible.
    op.execute(
        "UPDATE tenants SET name = 'Default Team', slug = 'default-team' "
        "WHERE tenant_id = 'demo-tenant-000'"
    )
    op.execute(
        "INSERT INTO tenants (tenant_id, name, slug, status, region, ner_enabled) "
        "SELECT 'demo-tenant-000', 'Default Team', 'default-team', 'active', "
        "'global', false "
        "WHERE NOT EXISTS (SELECT 1 FROM tenants WHERE tenant_id = 'demo-tenant-000')"
    )

    # ---------------- Step 2: add tenant columns to portal_users ----------
    with op.batch_alter_table("portal_users") as batch:
        batch.add_column(
            sa.Column(
                "tenant_id",
                sa.String(36),
                sa.ForeignKey(
                    "tenants.tenant_id",
                    name="fk_portal_users_tenant_id",
                    ondelete="RESTRICT",
                ),
                nullable=True,  # Flipped to NOT NULL in migration 030.
            )
        )
        batch.add_column(
            sa.Column(
                "tenant_role",
                sa.String(16),
                nullable=False,
                server_default=sa.text("'member'"),
            )
        )
        batch.add_column(
            sa.Column(
                "token_version",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )
    op.create_index(
        "idx_portal_users_tenant_created",
        "portal_users",
        ["tenant_id", "created_at"],
    )

    # ---------------- Step 3: tenant_invites table -----------------------
    op.create_table(
        "tenant_invites",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey(
                "tenants.tenant_id",
                name="fk_tenant_invites_tenant_id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "created_by",
            sa.String(36),
            sa.ForeignKey(
                "portal_users.id",
                name="fk_tenant_invites_created_by",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "used_by",
            sa.String(36),
            sa.ForeignKey(
                "portal_users.id",
                name="fk_tenant_invites_used_by",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
        # Display-only. NOT enforced at redemption time — invite codes
        # are bearer tokens.
        sa.Column("email_hint", sa.Text(), nullable=True),
    )
    op.create_index(
        "idx_tenant_invites_tenant_id", "tenant_invites", ["tenant_id"]
    )
    # ``code`` is already indexed via the UNIQUE constraint declared
    # on the column itself — no explicit redeem-lookup index needed.

    # ---------------- Step 4: backfill existing users ---------------------
    # Any portal_user without a tenant_id lands in the renamed tenant.
    # Safe because portal_users has no tenant_id column pre-migration,
    # so every row qualifies.
    op.execute(
        "UPDATE portal_users SET tenant_id = 'demo-tenant-000' "
        "WHERE tenant_id IS NULL"
    )

    # Promote the oldest portal user to owner. The subquery returns at
    # most one id; if the table is empty the UPDATE affects zero rows,
    # which is fine — the first post-cutover signup handles ownership
    # (see auth_service.signup refactor in a later commit).
    op.execute(
        "UPDATE portal_users SET tenant_role = 'owner' "
        "WHERE id = (SELECT id FROM portal_users "
        "            WHERE tenant_id = 'demo-tenant-000' "
        "            ORDER BY created_at ASC LIMIT 1)"
    )

    # ---------------- Step 5: invalidate pre-migration JWTs ---------------
    # Every existing portal JWT had token_version implicitly zero
    # (absent claim). The verify path rejects mismatched versions, so
    # bumping every row to 2 forces one-time re-login on deploy. Fresh
    # signups start at 1 via the column default.
    op.execute("UPDATE portal_users SET token_version = 2")


def downgrade() -> None:
    # Drop the invites table first (FK dependencies point at it).
    # Indexes on this table fall away with it — no explicit
    # drop_index needed.
    op.drop_table("tenant_invites")

    # Must drop the composite index BEFORE the columns it references
    # are gone, otherwise SQLite's batch_alter_table rebuild tries to
    # recreate the index on the post-drop table and fails with
    # "no such column: tenant_id".
    op.drop_index(
        "idx_portal_users_tenant_created", table_name="portal_users"
    )
    with op.batch_alter_table("portal_users") as batch:
        batch.drop_column("token_version")
        batch.drop_column("tenant_role")
        batch.drop_column("tenant_id")

    # Reverse the tenant rename. The row itself stays — deleting it
    # would cascade into projects/api_keys, which is not what we want.
    op.execute(
        "UPDATE tenants SET name = 'Demo Tenant', slug = 'demo' "
        "WHERE tenant_id = 'demo-tenant-000'"
    )
