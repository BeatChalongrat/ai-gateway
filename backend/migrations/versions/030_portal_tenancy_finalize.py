"""Phase 3 — Portal tenancy finalization (NOT NULL + request_logs FK).

Closes the last two schema gaps left open by migration 029:

1. ``portal_users.tenant_id`` flips to ``NOT NULL``. Migration 029
   backfilled every existing row; this constraint makes it impossible
   for a new row to forget tenancy. The application layer already
   treats ``None`` as invalid, so this is the belt to go with the
   braces.
2. ``request_logs.tenant_id`` gains a FK constraint pointing at
   ``tenants.tenant_id`` with ``ON DELETE RESTRICT``. Before adding
   the FK we scan for orphan rows (tenant_ids not present in
   ``tenants``) and insert placeholder tenant rows so the constraint
   can land without data loss (design doc §12 R3).

Why a separate migration from 029: Option C's correctness depends on
029's backfill having populated every ``portal_users.tenant_id`` row
before the NOT NULL lands. Splitting them lets an operator verify the
backfill actually ran (e.g., via ``SELECT COUNT(*) FROM portal_users
WHERE tenant_id IS NULL``) before taking the harder-to-reverse step.

Revision ID: 030
Revises: 029
Create Date: 2026-04-23
"""

from alembic import op
import sqlalchemy as sa


revision = "030"
down_revision = "029"
branch_labels = None
depends_on = None


_ORPHAN_PLACEHOLDER_PREFIX = "orphan-log-tenant-"


def upgrade() -> None:
    # ---------------- Step 1: orphan scan for request_logs --------------
    # Rows whose tenant_id doesn't resolve to a real tenant. Without a
    # placeholder insert, the FK in step 3 would reject them.
    conn = op.get_bind()
    orphans = conn.execute(
        sa.text(
            "SELECT DISTINCT tenant_id FROM request_logs "
            "WHERE tenant_id NOT IN (SELECT tenant_id FROM tenants)"
        )
    ).fetchall()
    for (orphan_tenant_id,) in orphans:
        # Placeholder rows are clearly marked so an operator can find
        # and reassign them later. Name is stable per-id so re-running
        # the migration (theoretically) wouldn't create duplicates.
        placeholder_name = f"{_ORPHAN_PLACEHOLDER_PREFIX}{orphan_tenant_id}"
        conn.execute(
            sa.text(
                "INSERT INTO tenants "
                "(tenant_id, name, slug, status, region, ner_enabled) "
                "VALUES "
                "(:tid, :name, :slug, 'inactive', 'global', false)"
            ),
            {
                "tid": orphan_tenant_id,
                "name": placeholder_name,
                "slug": placeholder_name,
            },
        )

    # ---------------- Step 2: portal_users.tenant_id NOT NULL ------------
    # Belt-and-braces guard: fail the migration loudly if any row still
    # has NULL tenant_id. The backfill in 029 should have handled every
    # row; this protects against a hand-edited DB that landed between
    # the two migrations.
    null_count = conn.execute(
        sa.text("SELECT COUNT(*) FROM portal_users WHERE tenant_id IS NULL")
    ).scalar_one()
    if null_count:
        raise RuntimeError(
            f"Cannot finalize portal tenancy: {null_count} portal_users "
            "rows still have NULL tenant_id. Run migration 029's backfill "
            "or assign tenants manually before retrying."
        )

    with op.batch_alter_table("portal_users") as batch:
        batch.alter_column("tenant_id", existing_type=sa.String(36), nullable=False)

    # ---------------- Step 3: request_logs.tenant_id FK ------------------
    # Batch mode is required for SQLite; on Postgres this compiles
    # down to a normal ALTER TABLE ADD CONSTRAINT.
    with op.batch_alter_table("request_logs") as batch:
        batch.create_foreign_key(
            "fk_request_logs_tenant_id",
            "tenants",
            ["tenant_id"],
            ["tenant_id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    # Drop FK first, then revert NOT NULL. Placeholder orphan tenants
    # are intentionally NOT removed — if an operator has reassigned
    # request_logs to them, deletion would cascade through RESTRICT
    # anyway. Leaving them in place is safe; downgrade is a rollback,
    # not a data-purge.
    with op.batch_alter_table("request_logs") as batch:
        batch.drop_constraint("fk_request_logs_tenant_id", type_="foreignkey")

    with op.batch_alter_table("portal_users") as batch:
        batch.alter_column("tenant_id", existing_type=sa.String(36), nullable=True)
