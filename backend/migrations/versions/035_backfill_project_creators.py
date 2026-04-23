"""Admin bug A1 — backfill projects.portal_user_id so the UI shows a
real creator instead of "—".

Pre-034 projects had no explicit creator linkage; the column existed
but was only populated for projects the portal self-service path
created. Admin-created projects (via POST /admin/projects) and seed
projects all landed with portal_user_id=NULL, which the new project-
detail modal renders as an em-dash.

Fix: for every project with a NULL portal_user_id, pick the earliest
owner of that project's tenant and write it. Results:

  * Alice's Acme projects → created_by = Alice (the acme Owner)
  * Bob's Globex projects → created_by = Bob
  * demo-tenant-000 projects → created_by = NULL still, because the
    seed demo tenant has no portal_user rows. The admin UI renders
    these as "System" (via the route layer, not the DB).

This is deliberately a *heuristic* backfill — we can't know who the
real creator was for pre-034 rows. The earliest-owner pick is the
least-wrong choice: that user had the authority to create anything
in the tenant at the time, and the audit log from 034+ will show
the real creator for every new row.

Revision ID: 035
Revises: 034
Create Date: 2026-04-24
"""

from alembic import op


revision = "035"
down_revision = "034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # For each project with a NULL portal_user_id, set it to the
    # earliest owner of the same tenant. If the tenant has no owner
    # (e.g. the admin-only demo-tenant-000 seed), leave NULL — the
    # route layer renders it as "System".
    op.execute(
        """
        UPDATE projects p
        SET portal_user_id = sub.owner_id
        FROM (
          SELECT
            pu.tenant_id,
            (
              SELECT pu2.id
              FROM portal_users pu2
              WHERE pu2.tenant_id = pu.tenant_id
                AND pu2.tenant_role = 'owner'
              ORDER BY pu2.created_at ASC
              LIMIT 1
            ) AS owner_id
          FROM portal_users pu
          GROUP BY pu.tenant_id
        ) AS sub
        WHERE p.tenant_id = sub.tenant_id
          AND sub.owner_id IS NOT NULL
          AND p.portal_user_id IS NULL
        """
    )

    # Now that projects have a creator set, backfill project_members
    # too — the migration-034 backfill ran before we had creators on
    # the newly-hydrated rows, so it missed these.
    op.execute(
        """
        INSERT INTO project_members (project_id, user_id, role, added_at)
        SELECT p.project_id, p.portal_user_id, 'admin', p.created_at
        FROM projects p
        WHERE p.portal_user_id IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM project_members pm
             WHERE pm.project_id = p.project_id
               AND pm.user_id = p.portal_user_id
          )
        """
    )


def downgrade() -> None:
    # Cannot undo the backfill — we don't know which rows were
    # originally NULL. The inverse of "set to earliest owner" would
    # need a before/after snapshot. Alembic allows an empty
    # downgrade; hard-rolling this migration back is a restore-from-
    # snapshot operation, not a code one.
    pass
