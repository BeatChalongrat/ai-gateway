"""Admin project-management UI (2026-04-24). Two additions:

1. ``project_members`` — explicit membership rows so a project can
   have more than its creator. Pre-034 the schema carried
   ``projects.portal_user_id`` as "the one user who made it" and
   treated every tenant user as having implicit access. That's fine
   for a single-team tenant; as soon as Alice wants to invite Bob
   into a subset of Acme's projects, we need explicit membership.

2. ``projects.archived_at`` — soft-delete marker for the new Archive
   action. 30-day restore window (tested separately). Replaces the
   DELETE flow for the admin UI; the hard-delete route stays so
   ops scripts can still hard-drop test fixtures.

Roles ('admin' | 'member' | 'viewer') match the spec's row-menu.
'admin' is the project-scoped admin (change policy, archive), not
the tenant role — deliberately named the same as the eventual RBAC
tier so the UI copy doesn't need rework when tenant roles gain an
admin.

Revision ID: 034
Revises: 033
Create Date: 2026-04-24
"""

from alembic import op
import sqlalchemy as sa


revision = "034"
down_revision = "033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -------------------------- projects.archived_at -------------------------
    with op.batch_alter_table("projects") as batch:
        batch.add_column(
            sa.Column(
                "archived_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )

    # -------------------------- project_members table ------------------------
    op.create_table(
        "project_members",
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column(
            "role",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'member'"),
        ),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "added_by",
            sa.String(36),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.project_id"],
            name="fk_project_members_project_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["portal_users.id"],
            name="fk_project_members_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "project_id",
            "user_id",
            name="pk_project_members",
        ),
    )
    op.create_index(
        "idx_project_members_user_id", "project_members", ["user_id"]
    )

    # -------------------------- backfill creators ----------------------------
    # Every existing project with a non-null portal_user_id gets an
    # admin-role row for that user. Projects with no portal_user_id
    # (admin-created via /admin/projects) stay memberless until an
    # owner attaches people.
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
    op.drop_index("idx_project_members_user_id", table_name="project_members")
    op.drop_table("project_members")
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("archived_at")
