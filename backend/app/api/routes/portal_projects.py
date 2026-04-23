"""Portal projects + API keys.

Every query here is scoped to ``portal_user_id = current_user.id``.
The backing tables (``projects``, ``api_keys``) are shared with
admin-managed resources — portal rows are distinguished purely by
having ``portal_user_id`` set.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.api.deps import get_db_session, get_portal_user
from app.api.portal_scope import scope_to_portal_tenant
from app.core.exceptions import (
    AuthorizationError,
    NotFoundError,
    ValidationError,
)
from app.models.api_key import ApiKeyORM
from app.models.portal_user import PortalUserORM
from app.models.project import ProjectORM
from app.services.api_key_service import ApiKeyService


router = APIRouter(dependencies=[Depends(get_portal_user)])
_api_key_service = ApiKeyService()


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


def _own_project(
    session: Session, user: PortalUserORM, project_id: str
) -> ProjectORM:
    stmt = select(ProjectORM).where(
        and_(
            ProjectORM.project_id == project_id,
            ProjectORM.portal_user_id == user.id,
        )
    )
    p = session.execute(stmt).scalar_one_or_none()
    if p is None:
        raise NotFoundError("Project not found.", code="project_not_found")
    return p


@router.get("")
@scope_to_portal_tenant
def list_projects(
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    rows = (
        session.execute(
            select(ProjectORM)
            .where(ProjectORM.portal_user_id == user.id)
            .order_by(ProjectORM.created_at.desc())
        )
        .scalars()
        .all()
    )
    return {"projects": [_to_dict(p) for p in rows]}


@router.post("")
@scope_to_portal_tenant
def create_project(
    body: ProjectCreate,
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    project = ProjectORM(
        project_id=str(uuid.uuid4()),
        tenant_id=_require_tenant_id(user),
        name=body.name,
        policy_profile_id="default",
        portal_user_id=user.id,
    )
    session.add(project)
    session.flush()
    return _to_dict(project)


@router.get("/{project_id}")
@scope_to_portal_tenant
def get_project(
    project_id: str,
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    return _to_dict(_own_project(session, user, project_id))


@router.patch("/{project_id}")
@scope_to_portal_tenant
def update_project(
    project_id: str,
    body: ProjectUpdate,
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    project = _own_project(session, user, project_id)
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
    project = _own_project(session, user, project_id)
    # Revoke all keys so they stop authenticating against the gateway.
    session.execute(
        select(ApiKeyORM).where(ApiKeyORM.project_id == project.project_id)
    )  # warm the join; then mark revoked individually
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
    _own_project(session, user, project_id)  # ownership check
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
    project = _own_project(session, user, project_id)
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


@router.delete("/{project_id}/keys/{key_id}")
@scope_to_portal_tenant
def delete_key(
    project_id: str,
    key_id: str,
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    _own_project(session, user, project_id)
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
