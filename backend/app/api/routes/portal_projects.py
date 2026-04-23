"""Portal projects + API keys.

Scope model (post-2026-04-24, hybrid / "Model C"):

* Tenant **Owner**: sees every project in their tenant, no explicit
  project_members row required. Tenant Owner short-circuits every
  permission check.
* Tenant **Member**: sees only projects where they have an explicit
  ``project_members`` row. Direct URL access to a project outside
  that set returns 403 ``project_access_denied``.
* Project members can hold role ``admin`` (manage members + keys),
  ``member`` (use keys), or ``viewer`` (read-only).

Auto-grants on project creation: the creator gets ``admin``; if
they're not already the tenant owner, the tenant owner is also
added as ``admin``. This keeps the "no project without at least
one admin" invariant without a DB trigger — application code does
the work and the test suite locks it in.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db_session, get_portal_user
from app.api.portal_scope import scope_to_portal_tenant
from app.core.exceptions import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.models.api_key import ApiKeyORM
from app.models.portal_user import PortalUserORM
from app.models.project import ProjectORM
from app.models.project_member import ProjectMemberORM
from app.services.api_key_service import ApiKeyService


router = APIRouter(dependencies=[Depends(get_portal_user)])
_api_key_service = ApiKeyService()


# ---------------------------------------------------------------------------
# Permission helpers.
# ---------------------------------------------------------------------------


_ROLE_ORDER = {"viewer": 0, "member": 1, "admin": 2}
_ALLOWED_ROLES = {"admin", "member", "viewer"}


def _project_member_role(
    session: Session, project_id: str, user_id: str
) -> str | None:
    """Return the caller's role on a project, or None if not a member."""
    row = session.execute(
        select(ProjectMemberORM.role).where(
            ProjectMemberORM.project_id == project_id,
            ProjectMemberORM.user_id == user_id,
        )
    ).scalar_one_or_none()
    return row


def _resolve_project(
    session: Session, user: PortalUserORM, project_id: str
) -> tuple[ProjectORM, str]:
    """Load a project + return the caller's effective role on it.

    Effective role resolution:
      * tenant owner → treated as "admin" on every project in their
        tenant, even without an explicit project_members row. This is
        the Model-C bypass Beat locked in.
      * explicit project_members row → the stored role.
      * no match → ``AuthorizationError(project_access_denied)``.
        404 would leak existence; 403 says "exists, you can't see it."

    Cross-tenant probes fall through to the 403 path too — an Acme
    user asking about a Globex project never gets told whether it
    exists, they just see the same opaque denial.
    """
    project = session.get(ProjectORM, project_id)
    if project is None:
        raise NotFoundError("Project not found.", code="project_not_found")
    if project.tenant_id != user.tenant_id:
        raise AuthorizationError(
            "You don't have access to this project. "
            "Ask the project admin.",
            code="project_access_denied",
        )

    is_tenant_owner = (user.tenant_role or "member") == "owner"
    if is_tenant_owner:
        return project, "admin"

    role = _project_member_role(session, project_id, user.id)
    if role is None:
        raise AuthorizationError(
            "You don't have access to this project. "
            "Ask the project admin.",
            code="project_access_denied",
        )
    return project, role


def _require_project_role(
    session: Session,
    user: PortalUserORM,
    project_id: str,
    *,
    minimum: str,
) -> ProjectORM:
    """Gate for mutating endpoints.

    ``minimum`` is a role name (``admin`` / ``member`` / ``viewer``);
    we compare effective role ordinal against it and 403 if too low.
    Tenant owner short-circuits to ``admin``.
    """
    project, role = _resolve_project(session, user, project_id)
    required = _ROLE_ORDER.get(minimum, 0)
    actual = _ROLE_ORDER.get(role, -1)
    if actual < required:
        raise AuthorizationError(
            f"This action requires project-{minimum} privileges.",
            code=f"project_{minimum}_required",
        )
    return project


def _require_tenant_id(user: PortalUserORM) -> str:
    """Read the tenant_id that every portal write has to land in.

    Post-Phase-3 migration, the DB has NOT NULL on portal_users.tenant_id
    (migration 030). This helper is the belt to go with those braces —
    any code path that reaches here without a tenant indicates
    something broke the Phase 3 contract (e.g. an admin-side script
    that nulled it out) and we refuse the request rather than silently
    landing a write in the wrong scope.
    """
    if not user.tenant_id:
        raise AuthorizationError(
            "Your account is not attached to a tenant. "
            "Contact your administrator.",
            code="user_without_tenant",
        )
    return user.tenant_id


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)


class ApiKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)


def _to_dict(p: ProjectORM) -> dict:
    return {
        "id": p.project_id,
        "name": p.name,
        "tenant_id": p.tenant_id,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }


@router.get("")
@scope_to_portal_tenant
def list_projects(
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    """List projects visible to the caller.

    Owner path: every non-archived project in the tenant.
    Member path: only projects where they have an explicit
    project_members row (any role).
    """
    tenant_id = _require_tenant_id(user)
    is_owner = (user.tenant_role or "member") == "owner"

    stmt = (
        select(ProjectORM)
        .where(ProjectORM.tenant_id == tenant_id)
        .where(ProjectORM.archived_at.is_(None))
        .order_by(ProjectORM.created_at.desc())
    )
    if not is_owner:
        stmt = stmt.join(
            ProjectMemberORM,
            and_(
                ProjectMemberORM.project_id == ProjectORM.project_id,
                ProjectMemberORM.user_id == user.id,
            ),
        )
    rows = session.execute(stmt).scalars().all()
    return {"projects": [_to_dict(p) for p in rows]}


@router.post("")
@scope_to_portal_tenant
def create_project(
    body: ProjectCreate,
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    """Create a project + auto-grant admin seats.

    Auto-grants (D4):
      * creator always gets ``admin``
      * tenant owner (if different) always gets ``admin`` too
    This invariant — "every project has at least one admin" — is
    enforced by application code rather than a DB trigger because
    adding the trigger would block legitimate migration-time
    inserts that don't flow through this handler.
    """
    tenant_id = _require_tenant_id(user)
    project = ProjectORM(
        project_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        name=body.name,
        policy_profile_id="default",
        portal_user_id=user.id,
    )
    session.add(project)
    session.flush()

    # Always grant the creator admin.
    session.add(
        ProjectMemberORM(
            project_id=project.project_id,
            user_id=user.id,
            role="admin",
            added_by=user.id,
        )
    )

    # If the creator isn't the tenant owner, pin the earliest owner
    # of the tenant onto the project too. Keeps the "tenant owner
    # can always jump in" mental model without having to rely on the
    # tenant-owner bypass alone.
    if (user.tenant_role or "member") != "owner":
        owner_row = session.execute(
            select(PortalUserORM.id)
            .where(PortalUserORM.tenant_id == tenant_id)
            .where(PortalUserORM.tenant_role == "owner")
            .order_by(PortalUserORM.created_at.asc())
            .limit(1)
        ).scalar_one_or_none()
        if owner_row is not None and owner_row != user.id:
            session.add(
                ProjectMemberORM(
                    project_id=project.project_id,
                    user_id=owner_row,
                    role="admin",
                    added_by=user.id,
                )
            )

    session.flush()
    return _to_dict(project)


@router.get("/{project_id}")
@scope_to_portal_tenant
def get_project(
    project_id: str,
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    project, _role = _resolve_project(session, user, project_id)
    return _to_dict(project)


@router.patch("/{project_id}")
@scope_to_portal_tenant
def update_project(
    project_id: str,
    body: ProjectUpdate,
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    project = _require_project_role(
        session, user, project_id, minimum="admin"
    )
    if body.name is not None:
        project.name = body.name
    # ProjectORM doesn't have a description column in this schema; kept
    # in the API surface for forward-compatibility but ignored here.
    session.flush()
    return _to_dict(project)


@router.delete("/{project_id}")
@scope_to_portal_tenant
def delete_project(
    project_id: str,
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    project = _require_project_role(
        session, user, project_id, minimum="admin"
    )
    # Revoke all keys so they stop authenticating against the gateway.
    for key in session.execute(
        select(ApiKeyORM).where(ApiKeyORM.project_id == project.project_id)
    ).scalars():
        key.status = "revoked"
    session.delete(project)
    session.flush()
    return {"message": "ok"}


# --- API keys scoped to a portal-owned project -----------------------------


@router.get("/{project_id}/keys")
@scope_to_portal_tenant
def list_keys(
    project_id: str,
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    _resolve_project(session, user, project_id)  # access check
    rows = (
        session.execute(
            select(ApiKeyORM)
            .where(ApiKeyORM.project_id == project_id)
            .where(ApiKeyORM.portal_user_id == user.id)
            .order_by(ApiKeyORM.created_at.desc())
        )
        .scalars()
        .all()
    )
    return {
        "keys": [
            {
                "key_id": k.key_id,
                "key_prefix": k.key_prefix,
                "name": k.name,
                "status": k.status,
                "created_at": k.created_at,
                "expires_at": k.expires_at,
                "last_used_at": k.last_used_at,
            }
            for k in rows
        ]
    }


@router.post("/{project_id}/keys")
@scope_to_portal_tenant
def create_key(
    project_id: str,
    body: ApiKeyCreate,
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    project = _require_project_role(
        session, user, project_id, minimum="member"
    )
    raw_key, api_key = _api_key_service.create_key(
        session,
        tenant_id=project.tenant_id,
        project_id=project.project_id,
        name=body.name,
        portal_user_id=user.id,
    )
    return {
        "key_id": api_key.key_id,
        "key_prefix": api_key.key_prefix,
        "name": api_key.name,
        "api_key": raw_key,  # shown exactly once
        "created_at": api_key.created_at,
    }


@router.post("/{project_id}/keys/{key_id}/rotate")
@scope_to_portal_tenant
def rotate_key(
    project_id: str,
    key_id: str,
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    """Rotate an API key without deleting it.

    Mints a new key value (returned once), keeps the same tenant /
    project / name / owner, and grace-expires the old key in 1 hour
    so callers have a window to roll their deployments. Ownership is
    verified via ``_require_project_role`` + a scoped match on
    ``api_keys.portal_user_id`` — cross-tenant rotation is
    structurally impossible.
    """
    _require_project_role(session, user, project_id, minimum="member")
    old = session.execute(
        select(ApiKeyORM).where(
            and_(
                ApiKeyORM.key_id == key_id,
                ApiKeyORM.project_id == project_id,
                ApiKeyORM.portal_user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if old is None:
        raise NotFoundError("API key not found.", code="key_not_found")

    rotated = _api_key_service.rotate_key(session, old.key_id)
    if rotated is None:
        # Key was revoked between the ownership check and the rotate
        # call — surface as not-found to match the 404 used elsewhere.
        raise NotFoundError("API key not found.", code="key_not_found")
    raw_new_key, new_orm = rotated
    return {
        "old_key_id": old.key_id,
        "old_key_expires_at": old.expires_at,
        "key_id": new_orm.key_id,
        "key_prefix": new_orm.key_prefix,
        "name": new_orm.name,
        "api_key": raw_new_key,  # shown exactly once
        "created_at": new_orm.created_at,
    }


@router.delete("/{project_id}/keys/{key_id}")
@scope_to_portal_tenant
def delete_key(
    project_id: str,
    key_id: str,
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    _require_project_role(session, user, project_id, minimum="member")
    key = session.execute(
        select(ApiKeyORM).where(
            and_(
                ApiKeyORM.key_id == key_id,
                ApiKeyORM.project_id == project_id,
                ApiKeyORM.portal_user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if key is None:
        raise NotFoundError("API key not found.", code="key_not_found")
    key.status = "revoked"
    session.flush()
    return {"message": "ok"}


# ---------------------------------------------------------------------------
# Project members — portal-side mirror of /admin/projects/.../members
# (Task: portal-project-members, 2026-04-24). The admin endpoints live
# under /admin/* and use admin_session auth; these live under /portal/*
# and use the portal JWT, with project-role gating on every write.
# ---------------------------------------------------------------------------


class ProjectMemberAdd(BaseModel):
    user_id: str
    role: str = "member"


class ProjectMemberUpdate(BaseModel):
    role: str


def _member_to_dict(row: ProjectMemberORM, u: PortalUserORM) -> dict:
    return {
        "user_id": u.id,
        "display_name": u.display_name,
        "email": u.email,
        "avatar_url": u.avatar_url,
        "role": row.role,
        "added_at": row.added_at.isoformat() if row.added_at else None,
    }


@router.get("/{project_id}/members")
@scope_to_portal_tenant
def list_project_members(
    project_id: str,
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    """Any project viewer can read the roster."""
    _resolve_project(session, user, project_id)
    rows = session.execute(
        select(ProjectMemberORM, PortalUserORM)
        .join(PortalUserORM, ProjectMemberORM.user_id == PortalUserORM.id)
        .where(ProjectMemberORM.project_id == project_id)
        .order_by(ProjectMemberORM.added_at.asc())
    ).all()
    return {
        "members": [_member_to_dict(pm, pu) for pm, pu in rows],
    }


@router.post("/{project_id}/members")
@scope_to_portal_tenant
def add_project_member(
    project_id: str,
    body: ProjectMemberAdd,
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    """Add a tenant-mate to the project.

    Requires project-admin (tenant owner passes via bypass). The
    target user must already live in the same tenant — cross-tenant
    membership is rejected at the service level, here we reject
    earlier to give a nicer error code.
    """
    project = _require_project_role(
        session, user, project_id, minimum="admin"
    )
    if body.role not in _ALLOWED_ROLES:
        raise ValidationError(
            f"role must be one of {sorted(_ALLOWED_ROLES)}",
            code="invalid_member_role",
        )
    target = session.get(PortalUserORM, body.user_id)
    if target is None:
        raise NotFoundError("User not found.", code="user_not_found")
    if (target.tenant_id or "") != project.tenant_id:
        raise ConflictError(
            "That user is not in your tenant.",
            code="cross_tenant_member_add",
        )

    row = ProjectMemberORM(
        project_id=project_id,
        user_id=body.user_id,
        role=body.role,
        added_by=user.id,
    )
    session.add(row)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise ConflictError(
            "That user is already a member of this project.",
            code="member_exists",
        )
    return _member_to_dict(row, target)


@router.patch("/{project_id}/members/{target_user_id}")
@scope_to_portal_tenant
def update_project_member(
    project_id: str,
    target_user_id: str,
    body: ProjectMemberUpdate,
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    """Change a member's role. Admin-only.

    Blocks demoting the last admin, just like the DELETE case — a
    project with zero admins would leave the Add/Remove-Member
    controls unreachable for anyone but a tenant owner.
    """
    _require_project_role(session, user, project_id, minimum="admin")

    if body.role not in _ALLOWED_ROLES:
        raise ValidationError(
            f"role must be one of {sorted(_ALLOWED_ROLES)}",
            code="invalid_member_role",
        )
    row = session.execute(
        select(ProjectMemberORM).where(
            ProjectMemberORM.project_id == project_id,
            ProjectMemberORM.user_id == target_user_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(
            "Member not found on this project.", code="member_not_found"
        )

    # Demotion guard: if this row is the last admin and the new role
    # isn't admin, refuse.
    if row.role == "admin" and body.role != "admin":
        _block_if_last_admin(session, project_id, target_user_id)

    row.role = body.role
    session.flush()
    target = session.get(PortalUserORM, target_user_id)
    return _member_to_dict(row, target) if target is not None else {
        "user_id": target_user_id,
        "role": row.role,
    }


@router.delete("/{project_id}/members/{target_user_id}")
@scope_to_portal_tenant
def remove_project_member(
    project_id: str,
    target_user_id: str,
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    """Remove a member.

    Two paths:
      * self-removal (Leave project) — any member can do it, but the
        last-admin guard still applies.
      * remove someone else — requires project-admin on the caller.
    """
    # Self-removal doesn't need project-admin, but does need access.
    self_leave = target_user_id == user.id
    if self_leave:
        _resolve_project(session, user, project_id)
    else:
        _require_project_role(session, user, project_id, minimum="admin")

    row = session.execute(
        select(ProjectMemberORM).where(
            ProjectMemberORM.project_id == project_id,
            ProjectMemberORM.user_id == target_user_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(
            "Member not found on this project.", code="member_not_found"
        )

    if row.role == "admin":
        _block_if_last_admin(session, project_id, target_user_id)

    session.delete(row)
    session.flush()
    return {"message": "ok"}


def _block_if_last_admin(
    session: Session, project_id: str, excluded_user_id: str
) -> None:
    """Refuse if removing/demoting would leave the project admin-less.

    Counts admins OTHER than the one being touched. If zero, raise
    409 ``last_admin_remaining``. The tenant-owner bypass means a
    project can still function without an admin row in practice, but
    we enforce the invariant at the data layer anyway so promotion
    flows (future RBAC changes) stay simple.
    """
    remaining = session.execute(
        select(func.count()).select_from(ProjectMemberORM).where(
            ProjectMemberORM.project_id == project_id,
            ProjectMemberORM.role == "admin",
            ProjectMemberORM.user_id != excluded_user_id,
        )
    ).scalar_one()
    if remaining < 1:
        raise ConflictError(
            "Cannot remove the last admin of this project. "
            "Promote another member first.",
            code="last_admin_remaining",
        )
