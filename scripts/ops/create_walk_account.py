"""The walk account — one permanent account for every walk, so no walk uses a person's own.

Run inside the backend container, with the address and password in the environment (they live in
.env only and are never printed):

    docker exec -i -e QA_EMAIL -e QA_PASSWORD <backend container> python - < scripts/ops/create_walk_account.py

What it makes, each only if it is not there yet (it never resets a password and never touches
another account):

  * a portal account, through the server's own sign-up service — so it is born the way any account
    is: its own tenant, of which it is the owner, and nothing of anyone else's. Password sign-in is
    closed on the deployment (PASSWORD_LOGIN_ENABLED off) and this account is not put on the
    allow-list: walks of the web chat sign their own token for it, as they do for the smoke account.
    The desktop app signs in through Google only, so this account does not reach it.
  * an admin-console account with role ``admin``, written to the admin audit log in the same
    transaction (the pattern of ``app.ops.first_admin``).

Its place on the chat organisation's access list (role admin) is set afterwards through the admin
door, which keeps its own audit line.

Prints what it did and the two ids a walk needs to sign a token. Nothing else.
"""

from __future__ import annotations

import os
import sys
import uuid

import bcrypt
from sqlalchemy import select

ACTOR = "ops:walk-account"


def main() -> int:
    from app.core.db import SessionLocal
    from app.models.admin_user import AdminUserORM
    from app.models.portal_user import PortalUserORM
    from app.services.admin_audit_service import AdminAuditService
    from app.services.portal import auth_service

    email = (os.environ.get("QA_EMAIL") or "").strip().lower()
    password = os.environ.get("QA_PASSWORD") or ""
    if "@" not in email or len(password) < 20:
        print("[walk-account] QA_EMAIL / QA_PASSWORD (20 characters or more) are not in the environment", file=sys.stderr)
        return 2
    session = SessionLocal()
    try:
        user = session.execute(select(PortalUserORM).where(PortalUserORM.email == email)).scalar_one_or_none()
        if user is None:
            auth_service.signup(session, email=email, password=password, display_name="QA walk account", tenant_name="QA walk account")
            user = session.execute(select(PortalUserORM).where(PortalUserORM.email == email)).scalar_one()
            user.email_verified = True
            portal = "created"
        else:
            portal = "exists"
        admin = session.execute(select(AdminUserORM).where(AdminUserORM.email == email)).scalar_one_or_none()
        if admin is None:
            username = "qa"
            if session.execute(select(AdminUserORM).where(AdminUserORM.username == username)).first():
                username = email
            admin = AdminUserORM(id=str(uuid.uuid4()), username=username, email=email,
                                 hashed_password=bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8"),
                                 role="admin", is_active=True)
            session.add(admin)
            session.flush()
            AdminAuditService().record(
                session, actor_key_id=ACTOR, tenant_id="__global__", action="admin_account.created",
                resource_type="admin_user", resource_id=admin.id,
                details={"email": admin.email, "role": "admin", "via": "walk-account", "why": "the walks' own permanent account"})
            console = "created"
        else:
            console = "exists"
        session.commit()
        print(f"[walk-account] portal account {portal} · admin-console account {console} (role {admin.role}, username {admin.username})")
        print(f"[walk-account] QA_USER_ID={user.id}")
        print(f"[walk-account] QA_TENANT_ID={user.tenant_id}")
        print(f"[walk-account] tenant role {user.tenant_role} · plan {user.plan} · verified {user.email_verified} · sign-in provider {user.oauth_provider or 'none'}")
        return 0
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
