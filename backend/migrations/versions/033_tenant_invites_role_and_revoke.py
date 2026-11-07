"""Invite-flow redesign — extend tenant_invites for link-based flow.

Portal Invite Flow Redesign (2026-04-24). Two changes:

1. ``role`` (default ``'member'``) — the role the invitee is granted on
   redemption. Current redeem_invite hardcodes ``'member'``; carrying
   it on the invite row lets a future owner decide "this link mints
   admins" without another schema change.

2. ``revoked_at`` (nullable DateTime) — soft-delete marker for the
   Revoke link flow. Pre-redesign we overloaded ``used_at`` for both
   redemption and revocation which made it impossible to tell "this
   code was used 4 times" from "this code was killed." Splitting the
   two keeps the audit trail honest.

Also switches the invite model from single-use to multi-use-until-
expiry: ``used_at``/``used_by`` still track the last redemption for
audit, but are no longer what makes a code dead. A code is live if
``revoked_at IS NULL`` AND ``expires_at > NOW()``.

Partial unique index guards the "one active invite per tenant" UX
contract — you cannot have two un-revoked invite rows for the same
tenant, so Regenerate must explicitly revoke first.

Revision ID: 033
Revises: 032
Create Date: 2026-04-24
"""

from alembic import op
import sqlalchemy as sa


revision = "033"
down_revision = "032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Add the two new columns. Both nullable-or-defaulted so existing
    #    rows pass the NOT NULL check without a data backfill.
    # ------------------------------------------------------------------
    with op.batch_alter_table("tenant_invites") as batch:
        batch.add_column(
            sa.Column(
                "role",
                sa.String(16),
                nullable=False,
                server_default=sa.text("'member'"),
            )
        )
        batch.add_column(
            sa.Column(
                "revoked_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )

    # ------------------------------------------------------------------
    # 2. Backfill. In the pre-redesign schema ``used_at IS NOT NULL``
    #    meant "dead" (either redeemed-and-done OR revoked — the code
    #    overloaded the column). Treat every such row as revoked in the
    #    new semantics so existing DBs pass the partial unique index
    #    below without needing a one-active-only constraint by hand.
    #    Also backfill already-expired rows so the "one live per
    #    tenant" UI never trips on historical clutter.
    # ------------------------------------------------------------------
    op.execute(
        "UPDATE tenant_invites SET revoked_at = COALESCE(used_at, expires_at) "
        "WHERE revoked_at IS NULL AND used_at IS NOT NULL"
    )
    op.execute(
        "UPDATE tenant_invites SET revoked_at = expires_at "
        "WHERE revoked_at IS NULL AND expires_at <= CURRENT_TIMESTAMP"
    )
    # If multiple rows for the same tenant survive (created before the
    # redesign, never used, not expired), keep the most recent and
    # revoke the older ones. Without this the CREATE UNIQUE INDEX
    # below would fail on legit data on long-lived dev DBs.
    op.execute(
        """
        UPDATE tenant_invites AS ti
        SET revoked_at = CURRENT_TIMESTAMP
        WHERE revoked_at IS NULL
          AND id NOT IN (
            SELECT DISTINCT ON (tenant_id) id
            FROM tenant_invites
            WHERE revoked_at IS NULL
            ORDER BY tenant_id, created_at DESC
          )
        """
    )

    # ------------------------------------------------------------------
    # 3. Partial unique index: one un-revoked invite per tenant.
    #    Expired-but-not-revoked rows still count as "present" for this
    #    constraint — the POST /invite handler auto-revokes them on the
    #    next create so the UI never shows a stale link. SQLite 3.8+
    #    supports partial indexes too, so dev tests don't fork schema.
    # ------------------------------------------------------------------
    op.create_index(
        "uq_tenant_invites_one_active_per_tenant",
        "tenant_invites",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
        sqlite_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_tenant_invites_one_active_per_tenant",
        table_name="tenant_invites",
    )
    with op.batch_alter_table("tenant_invites") as batch:
        batch.drop_column("revoked_at")
        batch.drop_column("role")
