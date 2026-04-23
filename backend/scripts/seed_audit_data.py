"""Seed data for the Phase 1.4 functional audit.

Creates:
- 2 admin tenants (Acme, Globex) with different regions + NER states
- 3 projects per tenant (6 total)
- 5 portal users with mixed states (3 active, 1 suspended, 1 unverified email)
- 3 token packs (Starter / Pro / Business)
- 1 admin user for /auth/login testing
- 2 keyword rules
- 1 alert rule (orphan-API probe)

Idempotent: checks before insert.

Run with audit env overrides:
    POSTGRES_PORT=5433 POSTGRES_DB=ai_gateway_audit \
    REDIS_PORT=6380 \
    JWT_SECRET=audit-dev-jwt-secret-not-for-prod \
    MASTER_ENCRYPTION_KEY=audit-dev-master-key-not-for-prod \
    PORTAL_JWT_SECRET=audit-dev-portal-secret-not-for-prod \
    APP_ENV=development \
    PYTHONPATH=. python -m scripts.seed_audit_data
"""
from __future__ import annotations

import json
import sys
import uuid

from sqlalchemy import select, text

sys.path.insert(0, ".")

from app.core.db import SessionLocal
from app.models.tenant import TenantORM
from app.models.project import ProjectORM
from app.models.api_key import ApiKeyORM
from app.services.api_key_service import ApiKeyService
import app.models  # noqa: F401


TENANTS = [
    {"tenant_id": "acme-001", "name": "Acme Corp", "slug": "acme",
     "region": "us-east-1", "ner_enabled": True, "status": "active"},
    {"tenant_id": "globex-001", "name": "Globex Inc", "slug": "globex",
     "region": "eu-west-1", "ner_enabled": False, "status": "active"},
]

PROJECTS_PER_TENANT = ["web-app", "mobile-app", "batch-jobs"]

PORTAL_USERS = [
    {"email": "alice@acme.test", "password": "Alice!2345", "display_name": "Alice Anderson",
     "tenant_id": "acme-001",
     "plan": "starter", "status": "active", "email_verified": True,
     "credit_balance": 50000, "credit_used": 0},
    {"email": "bob@globex.test", "password": "Bob!2345", "display_name": "Bob Beaumont",
     "tenant_id": "globex-001",
     "plan": "pro", "status": "active", "email_verified": True,
     "credit_balance": 200000, "credit_used": 15000},
    {"email": "charlie@acme.test", "password": "Charlie!2345",
     "display_name": "Charlie Chen 🐉 ทดสอบ",  # emoji + Thai in display_name
     "tenant_id": "acme-001",
     "plan": "free_trial", "status": "active", "email_verified": True,
     "credit_balance": 5000, "credit_used": 4900},  # low credit case
    {"email": "dave@acme.test", "password": "Dave!2345", "display_name": "Dave Davies",
     "tenant_id": "acme-001",
     "plan": "starter", "status": "suspended", "email_verified": True,
     "credit_balance": 10000, "credit_used": 2000},
    {"email": "eve@globex.test", "password": "Eve!2345", "display_name": "Eve Evans",
     "tenant_id": "globex-001",
     "plan": "free_trial", "status": "active", "email_verified": False,  # unverified
     "credit_balance": 5000, "credit_used": 0},
]

TOKEN_PACKS = [
    {"name": "Starter", "tokens": 100000, "price_usd": 10.0,
     "description": "For hobby projects", "sort_order": 1, "is_active": True},
    {"name": "Pro", "tokens": 500000, "price_usd": 40.0,
     "description": "Most popular", "sort_order": 2, "is_active": True},
    {"name": "Business", "tokens": 2000000, "price_usd": 150.0,
     "description": "For growing teams", "sort_order": 3, "is_active": True},
]


def _table_exists(session, name: str) -> bool:
    r = session.execute(text(
        "SELECT 1 FROM information_schema.tables WHERE table_name=:n"
    ), {"n": name}).first()
    return r is not None


def seed_tenants(session):
    created = []
    for t in TENANTS:
        existing = session.get(TenantORM, t["tenant_id"])
        if existing is not None:
            continue
        session.add(TenantORM(**t))
        created.append(t["tenant_id"])
    session.flush()
    return created


def seed_projects(session):
    created = []
    for tenant in TENANTS:
        for name in PROJECTS_PER_TENANT:
            pid = f"{tenant['slug']}-{name}"
            existing = session.get(ProjectORM, pid)
            if existing is not None:
                continue
            session.add(ProjectORM(
                project_id=pid,
                tenant_id=tenant["tenant_id"],
                name=f"{tenant['name']} — {name}",
                policy_profile_id="default",
            ))
            created.append(pid)
    session.flush()
    return created


def seed_api_keys(session):
    """One active API key per project. Returns list of (project_id, raw_key)."""
    svc = ApiKeyService()
    out = []
    for tenant in TENANTS:
        for name in PROJECTS_PER_TENANT:
            pid = f"{tenant['slug']}-{name}"
            existing = session.execute(
                select(ApiKeyORM).where(
                    ApiKeyORM.project_id == pid,
                    ApiKeyORM.status == "active",
                )
            ).scalar_one_or_none()
            if existing is not None:
                continue
            raw_key, api_key = svc.create_key(
                session,
                tenant_id=tenant["tenant_id"],
                project_id=pid,
                name=f"seeded-{name}",
            )
            out.append((pid, raw_key))
    session.flush()
    return out


def seed_portal_users(session):
    import bcrypt
    from app.models.portal_user import PortalUserORM

    def _hash(pw: str) -> str:
        return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("utf-8")

    created = []
    for u in PORTAL_USERS:
        existing = session.execute(
            select(PortalUserORM).where(PortalUserORM.email == u["email"])
        ).scalar_one_or_none()
        if existing is not None:
            continue
        user = PortalUserORM(
            id=str(uuid.uuid4()),
            email=u["email"],
            password_hash=_hash(u["password"]),
            display_name=u["display_name"],
            tenant_id=u["tenant_id"],
            plan=u["plan"],
            status=u["status"],
            email_verified=u["email_verified"],
            credit_balance=u["credit_balance"],
            credit_used=u["credit_used"],
        )
        session.add(user)
        session.flush()
        created.append(u["email"])
    return created


def seed_token_packs(session):
    from app.models.token_pack import TokenPackORM
    created = []
    for p in TOKEN_PACKS:
        existing = session.execute(
            select(TokenPackORM).where(TokenPackORM.name == p["name"])
        ).scalar_one_or_none()
        if existing is not None:
            continue
        session.add(TokenPackORM(
            id=str(uuid.uuid4()),
            name=p["name"],
            tokens=p["tokens"],
            price_usd=p["price_usd"],
            description=p["description"],
            sort_order=p["sort_order"],
            is_active=p["is_active"],
        ))
        created.append(p["name"])
    session.flush()
    return created


def seed_admin_user(session):
    """Seed the default admin user so /auth/login is usable."""
    import bcrypt
    from app.models.admin_user import AdminUserORM
    existing = session.execute(
        select(AdminUserORM).where(AdminUserORM.username == "admin")
    ).scalar_one_or_none()
    if existing is not None:
        return False
    hashed = bcrypt.hashpw("admin1234".encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("utf-8")
    session.add(AdminUserORM(
        id=str(uuid.uuid4()),
        username="admin",
        email="admin@audit.test",
        hashed_password=hashed,
        role="super_admin",
        is_active=True,
    ))
    session.flush()
    return True


def main():
    session = SessionLocal()
    summary = {}
    try:
        summary["tenants_created"] = seed_tenants(session)
        summary["projects_created"] = seed_projects(session)
        summary["api_keys"] = seed_api_keys(session)
        summary["portal_users_created"] = seed_portal_users(session)
        summary["token_packs_created"] = seed_token_packs(session)
        summary["admin_user_created"] = seed_admin_user(session)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
