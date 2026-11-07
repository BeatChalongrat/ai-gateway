"""FIX88 Part A — a per-user credit ceiling.

``portal_users.credit_quota``: the ceiling a user's balance is filled
TO. Until now every screen derived "total" as ``credit_balance +
credit_used``, so a top-up inflated the denominator and "full" could
never be reached. The ceiling is a stored fact from here on; the
balance moves through ``credit_service`` only (capped top-up, fill to
the ceiling, grants that raise the ceiling, ceiling changes that move
the balance by the same delta).

Backfill: ``credit_quota = credit_balance + credit_used`` — the exact
value every screen derived until now, so on day one every user's ring,
"of N" and percent read what they read yesterday. Nothing else on the
row is touched; no ledger row is written (no balance moved).

Reversible: the downgrade drops the column; balance, used and the
ledger survive untouched.
"""

from alembic import op
import sqlalchemy as sa

revision = "088"
down_revision = "087"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portal_users",
        sa.Column(
            "credit_quota",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    # The value every screen derived until now — day one shows exactly
    # what it showed yesterday.
    op.execute(
        "UPDATE portal_users SET credit_quota = credit_balance + credit_used"
    )


def downgrade() -> None:
    op.drop_column("portal_users", "credit_quota")
