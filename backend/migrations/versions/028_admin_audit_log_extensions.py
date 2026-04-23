"""Admin audit log: add outcome + request metadata columns (Phase 2A Commit 7).

Before this migration the ``admin_audit_log`` table captured the bare
minimum — who, what, where in the resource tree — but no information
about the *result* of the action or the *caller context*. That makes
post-mortem work (suspicious activity review, cross-tenant access
audits, rate-limit trend analysis) much harder than it needs to be.

New columns:

* ``outcome`` (VARCHAR(16)) — "success" | "denied" | "error". NULL for
  rows written by older code paths.
* ``ip_address`` (VARCHAR(45)) — string form of the caller's IP.
  IPv6-sized so both v4 and v6 fit.
* ``user_agent`` (VARCHAR(500)) — truncated UA string.
* ``session_id_prefix`` (VARCHAR(16)) — first 8 hex chars of the
  admin session UUID, indexed. We store the prefix only so a leaked
  audit log cannot be used to hijack active sessions, while still
  allowing an operator to correlate a burst of events to the same
  session when investigating.

All new columns are nullable so the migration is safe on a table
with existing rows.

Revision ID: 028
Revises: 027
Create Date: 2026-04-23
"""

from alembic import op
import sqlalchemy as sa


revision = "028"
down_revision = "027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("admin_audit_log") as batch:
        batch.add_column(sa.Column("outcome", sa.String(16), nullable=True))
        batch.add_column(sa.Column("ip_address", sa.String(45), nullable=True))
        batch.add_column(sa.Column("user_agent", sa.String(500), nullable=True))
        batch.add_column(sa.Column("session_id_prefix", sa.String(16), nullable=True))
    # Separate index on session_id_prefix for correlation queries.
    op.create_index(
        "ix_admin_audit_log_session_id_prefix",
        "admin_audit_log",
        ["session_id_prefix"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_admin_audit_log_session_id_prefix",
        table_name="admin_audit_log",
    )
    with op.batch_alter_table("admin_audit_log") as batch:
        batch.drop_column("session_id_prefix")
        batch.drop_column("user_agent")
        batch.drop_column("ip_address")
        batch.drop_column("outcome")
