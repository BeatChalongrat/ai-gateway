"""FIX132 C — a user can delete their own account.

Two nullable timestamps on ``portal_users``:

* ``deletion_requested_at`` — when the user confirmed "delete my account".
  From that moment ``status`` is ``pending_deletion``: sign-in, refresh and
  every portal route refuse the account, every token issued before is dead
  (``token_version`` bump), and chat-edge has hidden the user's chats. The
  nightly retention pipeline purges the account ``account_purge_days``
  (30) later.
* ``deleted_at`` — when that purge ran. The row stays as an anonymised
  tombstone (``status = 'deleted'``, no email, no name, no password, no
  OAuth link) so the credit ledger and the request-log rows that point at
  it keep a valid key; nothing in it identifies the person any more.

The partial index serves the nightly "whose window has passed" scan.

Idempotent: every statement is IF NOT EXISTS, so a re-run (or a database
where the columns were added by hand) is a no-op. Reversible: the
downgrade drops the index and both columns; no other row is touched.

Revision ID: 089
Revises: 088
"""

from alembic import op

revision = "089"
down_revision = "088"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE portal_users "
        "ADD COLUMN IF NOT EXISTS deletion_requested_at TIMESTAMPTZ NULL"
    )
    op.execute(
        "ALTER TABLE portal_users ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_portal_users_deletion_requested_at "
        "ON portal_users (deletion_requested_at) "
        "WHERE deletion_requested_at IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_portal_users_deletion_requested_at")
    op.execute("ALTER TABLE portal_users DROP COLUMN IF EXISTS deleted_at")
    op.execute("ALTER TABLE portal_users DROP COLUMN IF EXISTS deletion_requested_at")
