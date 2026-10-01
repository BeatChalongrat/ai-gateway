"""Admin-ui accounts and their roles (AM9).

Granting a role that matters used to be possible only by SQL (AM7's report:
``update admin_users set role='agent_publisher' where email=…``) — so it would
either never be used or be done wrong late at night. This is the admin app's
API for it:

* only an admin (role ``admin`` or ``super_admin``, from the signed session)
  may list, create, change or deactivate an account, or set a new password;
* every change is written to the admin audit log in the same transaction —
  who, which account, the old value and the new one (never a password);
* nobody can lower their own role or deactivate themselves, and the last
  active admin can be neither demoted nor deactivated;
* a role change, a deactivation or a new password ends that account's open
  sessions, so the new state holds from its next request, not its next
  sign-in a week later.

The CSRF middleware covers this router (its paths start with ``/admin/``).
"""

from __future__ import annotations

import re
import uuid

import bcrypt
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.admin_auth import get_admin_session
from app.api.deps import get_db_session
from app.core.exceptions import AuthorizationError, NotFoundError, ValidationError
from app.models.admin_user import AdminUserORM
from app.services.admin.session_service import get_session_manager
from app.services.admin_audit_service import AdminAuditService

router = APIRouter(prefix="/admin/accounts", tags=["admin-accounts"])

# The roles the admin app knows (admin-ui lib/auth.ts), most to least access.
ROLES = ("admin", "tenant_admin", "project_admin", "agent_publisher", "viewer")
ADMIN_ROLES = frozenset({"admin", "super_admin"})
MIN_PASSWORD = 12
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_USERNAME = re.compile(r"^[A-Za-z0-9._@-]{2,100}$")


def require_account_admin(session: dict = Depends(get_admin_session)) -> dict:
    """The signed-in admin-ui account, if it may manage accounts (admin or super_admin)."""
    if str(session.get("role") or "").lower() not in ADMIN_ROLES:
        raise AuthorizationError("Only an admin may manage admin-ui accounts.",
                                 details={"role": session.get("role"), "required": "admin"})
    return session


def _out(u: AdminUserORM) -> dict:
    return {"id": u.id, "username": u.username, "email": u.email, "role": u.role, "is_active": bool(u.is_active),
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "updated_at": u.updated_at.isoformat() if u.updated_at else None}


def _audit(db: Session, me: dict, action: str, user: AdminUserORM, details: dict) -> None:
    AdminAuditService().record(
        db, actor_key_id=f"session:{me.get('user_id', '?')}", tenant_id=me.get("tenant_id") or "__global__",
        action=action, resource_type="admin_user", resource_id=user.id,
        details={"email": user.email, **details, "via": "admin-ui"})


def _active_admins(db: Session) -> int:
    return int(db.execute(select(func.count()).select_from(AdminUserORM).where(
        AdminUserORM.is_active.is_(True), AdminUserORM.role.in_(sorted(ADMIN_ROLES)))).scalar_one())


def _hash(password: str) -> str:
    if len(password or "") < MIN_PASSWORD:
        raise ValidationError(f"The password must be at least {MIN_PASSWORD} characters.", code="weak_password")
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _end_sessions(manager, user_id: str) -> int:
    try:
        return int(manager.revoke_all_for_user(user_id) or 0)
    except Exception:   # noqa: BLE001 — the change stands; an unreachable store only delays it
        return 0


class CreateAccount(BaseModel):
    email: str
    role: str
    password: str
    username: str | None = None


class ChangeAccount(BaseModel):
    role: str | None = None
    is_active: bool | None = None


class NewPassword(BaseModel):
    password: str


@router.get("")
def list_accounts(me: dict = Depends(require_account_admin), db: Session = Depends(get_db_session)):
    rows = db.execute(select(AdminUserORM).order_by(AdminUserORM.email)).scalars().all()
    return {"accounts": [_out(u) for u in rows], "roles": list(ROLES), "me": me.get("user_id")}


@router.post("", status_code=201)
def create_account(body: CreateAccount, me: dict = Depends(require_account_admin), db: Session = Depends(get_db_session)):
    email = body.email.strip().lower()
    if not _EMAIL.match(email):
        raise ValidationError("Enter a valid e-mail address.", code="bad_email")
    if body.role not in ROLES:
        raise ValidationError("Unknown role.", code="bad_role", details={"roles": list(ROLES)})
    username = (body.username or email).strip()
    if not _USERNAME.match(username):
        raise ValidationError("A username has 2–100 letters, digits or . _ @ -", code="bad_username")
    taken = db.execute(select(AdminUserORM).where((AdminUserORM.email == email) | (AdminUserORM.username == username))).first()
    if taken:
        raise ValidationError("An account with this e-mail or username exists.", code="account_exists")
    user = AdminUserORM(id=str(uuid.uuid4()), username=username, email=email, hashed_password=_hash(body.password),
                        role=body.role, is_active=True, org_id=me.get("tenant_id"))
    db.add(user)
    db.flush()
    _audit(db, me, "admin_account.created", user, {"role": body.role})
    return {"account": _out(user)}


@router.patch("/{user_id}")
def change_account(user_id: str, body: ChangeAccount, me: dict = Depends(require_account_admin),
                   db: Session = Depends(get_db_session), manager=Depends(get_session_manager)):
    user = db.get(AdminUserORM, user_id)
    if user is None:
        raise NotFoundError("No such account.")
    self_change = user.id == me.get("user_id")
    changed = False
    if body.role is not None and body.role != user.role:
        if body.role not in ROLES:
            raise ValidationError("Unknown role.", code="bad_role", details={"roles": list(ROLES)})
        if self_change:
            raise ValidationError("You cannot change your own role.", code="own_role")
        if user.role in ADMIN_ROLES and body.role not in ADMIN_ROLES and user.is_active and _active_admins(db) <= 1:
            raise ValidationError("This is the last active admin.", code="last_admin")
        _audit(db, me, "admin_account.role_changed", user, {"from": user.role, "to": body.role})
        user.role = body.role
        changed = True
    if body.is_active is not None and body.is_active != bool(user.is_active):
        if self_change and not body.is_active:
            raise ValidationError("You cannot deactivate yourself.", code="own_account")
        if not body.is_active and user.role in ADMIN_ROLES and _active_admins(db) <= 1:
            raise ValidationError("This is the last active admin.", code="last_admin")
        _audit(db, me, "admin_account.activated" if body.is_active else "admin_account.deactivated", user,
               {"from": bool(user.is_active), "to": body.is_active})
        user.is_active = body.is_active
        changed = True
    db.flush()
    ended = _end_sessions(manager, user.id) if changed else 0
    return {"account": _out(user), "sessions_ended": ended}


@router.post("/{user_id}/password")
def set_password(user_id: str, body: NewPassword, me: dict = Depends(require_account_admin),
                 db: Session = Depends(get_db_session), manager=Depends(get_session_manager)):
    user = db.get(AdminUserORM, user_id)
    if user is None:
        raise NotFoundError("No such account.")
    user.hashed_password = _hash(body.password)
    _audit(db, me, "admin_account.password_set", user, {"self": user.id == me.get("user_id")})
    db.flush()
    return {"account": _out(user), "sessions_ended": _end_sessions(manager, user.id)}
