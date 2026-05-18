"""Add compound indexes on request_logs for dashboard + cost queries.

Revision ID: 040
Revises: 039
Create Date: 2026-05-15

The four indexes added here fix the sub-optimal plans the May 2026
scalability audit caught:

* ``ix_request_logs_tenant_created`` (tenant_id, created_at DESC) — fixes
  the in-memory sort on tenant-scoped recent-requests queries.
* ``ix_request_logs_project_created`` (project_id, created_at DESC) —
  symmetric for project-scoped dashboards.
* ``ix_request_logs_project_status`` (project_id, status) — covers
  policy filters (``WHERE status = 'masked'``) without a heap re-check
  on every project row.
* ``ix_request_logs_tenant_cost`` (tenant_id, created_at DESC) INCLUDE
  (estimated_cost_usd) — covering index for monthly tenant cost
  aggregations; turns the SUM into an index-only scan.

All four are created CONCURRENTLY so the migration takes no table-level
lock — safe to run on a live database. This is the reason both
``upgrade()`` and ``downgrade()`` set ``autocommit_block`` and use raw
``CREATE INDEX CONCURRENTLY`` / ``DROP INDEX CONCURRENTLY``; the
Alembic ``op.create_index`` helper wraps in a transaction by default,
which Postgres rejects for ``CONCURRENTLY``.
"""

from alembic import op

revision = "040"
down_revision = "039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CREATE INDEX CONCURRENTLY cannot run inside a transaction.
    # ``autocommit_block`` exits the migration's transaction for the
    # duration of the block so each statement commits on its own.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_request_logs_tenant_created "
            "ON request_logs (tenant_id, created_at DESC)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_request_logs_project_created "
            "ON request_logs (project_id, created_at DESC)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_request_logs_project_status "
            "ON request_logs (project_id, status)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_request_logs_tenant_cost "
            "ON request_logs (tenant_id, created_at DESC) "
            "INCLUDE (estimated_cost_usd)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS ix_request_logs_tenant_cost"
        )
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS ix_request_logs_project_status"
        )
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS ix_request_logs_project_created"
        )
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS ix_request_logs_tenant_created"
        )
