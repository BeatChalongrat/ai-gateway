"""Make sure the configured first admin exists — on a deployment that already has accounts.

The startup seed only acts on an empty ``admin_users`` table. A deployment
whose first admin was created under another address (an old default, a person
who left) moves to its own configured one with this command, without a moment
with no usable admin:

    docker compose exec backend python -m app.ops.first_admin ensure
    # sign in as INITIAL_ADMIN_EMAIL, then deactivate the old account on the Accounts page

``ensure`` creates the account named by INITIAL_ADMIN_EMAIL /
INITIAL_ADMIN_PASSWORD (``app.core.first_admin``) with role ``admin`` when no
account has that e-mail, and changes nothing otherwise: it never resets a
password, never reactivates and never touches another account. The creation
is written to the admin audit log in the same transaction. The password is
never printed.

``status`` prints the active admins (e-mail and role only).
"""

from __future__ import annotations

import sys
import uuid

import bcrypt
from sqlalchemy import select

from app.core.first_admin import FirstAdminNotConfigured, first_admin_from_env

ADMIN_ROLES = ("admin", "super_admin")
ACTOR = "system:first-admin"


def ensure(session) -> tuple[str, str]:
    """(``created`` | ``exists``, e-mail). Raises ``FirstAdminNotConfigured`` when the env names nobody."""
    from app.models.admin_user import AdminUserORM
    from app.services.admin_audit_service import AdminAuditService

    first = first_admin_from_env()
    if session.execute(select(AdminUserORM).where(AdminUserORM.email == first.email)).first():
        return "exists", first.email
    username = first.username
    if session.execute(select(AdminUserORM).where(AdminUserORM.username == username)).first():
        username = first.email   # the configured username belongs to another account; the e-mail is unique
    user = AdminUserORM(
        id=str(uuid.uuid4()), username=username, email=first.email,
        hashed_password=bcrypt.hashpw(first.password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8"),
        role="admin", is_active=True)
    session.add(user)
    session.flush()
    AdminAuditService().record(
        session, actor_key_id=ACTOR, tenant_id="__global__", action="admin_account.created",
        resource_type="admin_user", resource_id=user.id,
        details={"email": user.email, "role": "admin", "via": "first-admin"})
    return "created", first.email


def active_admins(session) -> list[tuple[str, str]]:
    from app.models.admin_user import AdminUserORM

    rows = session.execute(select(AdminUserORM.email, AdminUserORM.role).where(
        AdminUserORM.is_active.is_(True), AdminUserORM.role.in_(ADMIN_ROLES)).order_by(AdminUserORM.email)).all()
    return [(r[0], r[1]) for r in rows]


def main(argv: list[str]) -> int:
    from app.core.db import SessionLocal

    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd not in ("ensure", "status"):
        print("usage: python -m app.ops.first_admin ensure|status", file=sys.stderr)
        return 2
    session = SessionLocal()
    try:
        if cmd == "ensure":
            try:
                outcome, email = ensure(session)
            except FirstAdminNotConfigured as e:
                print(f"[first-admin] not configured: {e}", file=sys.stderr)
                return 1
            session.commit()
            print(f"[first-admin] {email}: {outcome}")
        for email, role in active_admins(session):
            print(f"[first-admin] active admin: {email} ({role})")
        return 0
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
