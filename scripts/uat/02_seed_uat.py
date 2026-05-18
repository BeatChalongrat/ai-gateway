"""UAT seed — populate a freshly truncated DB with the data the customer needs.

Run AFTER ``01_truncate.sql``. Idempotent for tenants/projects/portal users
(``ON CONFLICT DO NOTHING`` semantics via ``session.get`` checks), so a partial
run can be re-executed safely.

Run from inside the backend container:

    docker exec -it ai-gateway-backend-1 python -m scripts.uat.seed_uat

Or as a one-liner if the directory isn't a package on the path:

    docker exec -it ai-gateway-backend-1 \\
        python /work/scripts/uat/02_seed_uat.py

What it seeds
-------------
* admin user        — admin / admin1234 (matches backend auto-seed behaviour)
* model_configs     — re-seed from SEED_CANDIDATES (alembic 037 won't re-run)
* tenants           — demo-tenant-000 (portal compat) + acme-corp-001 + techstart-001
* projects          — 3 projects per task spec
* api_keys          — 1 per project (raw keys printed once to stdout)
* portal_users      — 4 demo accounts (somchai/suda/wichai/malee, all Demo@2025!)
* policy_profiles   — "strict" profile (mask PII)
* portal credits    — credit_balance pre-loaded per owner

Output: prints a credentials summary at the end suitable for copy-paste into
the customer handoff doc.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from decimal import Decimal

import bcrypt
import sqlalchemy as sa
from sqlalchemy.orm import Session

# Make the backend package importable in two layouts:
#  - host:       <repo>/backend/app/...
#  - container:  /app/app/...      (backend image WORKDIR is /app)
HERE = os.path.dirname(os.path.abspath(__file__))
_CANDIDATE_ROOTS = [
    os.path.abspath(os.path.join(HERE, "..", "..", "backend")),
    "/app",
]
for _root in _CANDIDATE_ROOTS:
    if os.path.isdir(os.path.join(_root, "app")) and _root not in sys.path:
        sys.path.insert(0, _root)

from app.core.db import SessionLocal  # noqa: E402
import app.models  # noqa: E402,F401  - registers all ORM models
from app.models.admin_user import AdminUserORM  # noqa: E402
from app.models.api_key import ApiKeyORM  # noqa: E402
from app.models.model_config import ModelConfigORM  # noqa: E402
from app.models.policy import PolicyORM  # noqa: E402
from app.models.portal_user import PortalUserORM  # noqa: E402
from app.models.project import ProjectORM  # noqa: E402
from app.models.tenant import TenantORM  # noqa: E402
from app.services.api_key_service import ApiKeyService  # noqa: E402
from app.services.providers.model_registry import SEED_CANDIDATES  # noqa: E402


PORTAL_USER_PASSWORD = "Demo@2025!"
ADMIN_PASSWORD = "admin1234"

TENANTS = [
    # demo-tenant-000 stays so portal project creation (hardcoded in P0-4)
    # keeps working; remove once PORTAL_TENANCY_MIGRATION_PLAN lands.
    {
        "tenant_id": "demo-tenant-000",
        "name": "Demo Tenant",
        "slug": "demo",
        "status": "active",
        "region": "global",
        "ner_enabled": False,
    },
    {
        "tenant_id": "acme-corp-001",
        "name": "Acme Corporation",
        "slug": "acme",
        "status": "active",
        "region": "th",
        "ner_enabled": True,
    },
    {
        "tenant_id": "techstart-001",
        "name": "TechStart",
        "slug": "techstart",
        "status": "active",
        "region": "th",
        "ner_enabled": True,
    },
]

PROJECTS = [
    {
        "project_id": "acme-customer-support",
        "tenant_id": "acme-corp-001",
        "name": "Customer Support Bot",
        "policy_profile_id": "default",
    },
    {
        "project_id": "acme-internal-assistant",
        "tenant_id": "acme-corp-001",
        "name": "Internal Assistant",
        "policy_profile_id": "strict",
    },
    {
        "project_id": "techstart-chatbot-mvp",
        "tenant_id": "techstart-001",
        "name": "AI Chatbot MVP",
        "policy_profile_id": "default",
    },
]

PORTAL_USERS = [
    {
        "tenant_id": "acme-corp-001",
        "email": "somchai@acme.ai.gateway",
        "display_name": "สมชาย ใจดี",
        "tenant_role": "owner",
        "credit_balance": 1_000_000,
    },
    {
        "tenant_id": "acme-corp-001",
        "email": "suda@acme.ai.gateway",
        "display_name": "สุดา รักงาน",
        "tenant_role": "member",
        "credit_balance": 0,
    },
    {
        "tenant_id": "techstart-001",
        "email": "wichai@techstart.ai.gateway",
        "display_name": "วิชัย เทคโนโลยี",
        "tenant_role": "owner",
        "credit_balance": 500_000,
    },
    {
        "tenant_id": "techstart-001",
        "email": "malee@techstart.ai.gateway",
        "display_name": "มาลี สมาร์ท",
        "tenant_role": "member",
        "credit_balance": 0,
    },
]

POLICY_PROFILES = [
    {
        "profile_id": "strict",
        "name": "Strict — Mask PII",
        "action": "mask",
        "rules_json": json.dumps(
            [
                {"category": "pii", "action": "mask"},
                {"category": "financial", "action": "mask"},
                {"category": "identity", "action": "block"},
            ]
        ),
    },
]


def _hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def seed_admin(session: Session) -> tuple[bool, str]:
    existing = session.execute(sa.select(AdminUserORM).limit(1)).scalar_one_or_none()
    if existing is not None:
        return False, existing.username
    row = AdminUserORM(
        id=str(uuid.uuid4()),
        username="admin",
        email="admin@example.com",
        hashed_password=_hash_password(ADMIN_PASSWORD),
        role="admin",
        is_active=True,
    )
    session.add(row)
    session.flush()
    return True, row.username


def seed_model_configs(session: Session) -> int:
    """Re-seed model_configs from SEED_CANDIDATES.

    Migration 037 only runs on a fresh alembic upgrade. After a TRUNCATE we
    have to re-populate manually so the registry boots with real candidates.
    Idempotent via the (provider, model) unique constraint.
    """
    inserted = 0
    for c in SEED_CANDIDATES:
        exists = session.execute(
            sa.select(ModelConfigORM.id).where(
                ModelConfigORM.provider == c.provider,
                ModelConfigORM.model == c.model,
            )
        ).scalar_one_or_none()
        if exists is not None:
            continue
        session.add(
            ModelConfigORM(
                provider=c.provider,
                model=c.model,
                supports_streaming=c.supports_streaming,
                supports_tools=False,
                supports_vision=False,
                max_context_tokens=128000,
                cost_tier=c.cost_tier,
                latency_tier=c.latency_tier,
                cost_per_input_token=c.cost_per_input_token,
                cost_per_output_token=c.cost_per_output_token,
                source="seed",
                is_manual_override=False,
                is_active=True,
            )
        )
        inserted += 1
    session.flush()
    return inserted


def seed_tenants(session: Session) -> int:
    inserted = 0
    for t in TENANTS:
        # Two unique columns to consider: tenant_id (PK) and slug. Skip if
        # either collides so a partially-seeded DB can be topped up safely.
        if session.get(TenantORM, t["tenant_id"]) is not None:
            continue
        slug_taken = session.execute(
            sa.select(TenantORM.tenant_id).where(TenantORM.slug == t["slug"])
        ).scalar_one_or_none()
        if slug_taken is not None:
            print(
                f"[warn] slug '{t['slug']}' already owned by tenant "
                f"'{slug_taken}' — skipping insert of '{t['tenant_id']}'"
            )
            continue
        session.add(TenantORM(**t))
        inserted += 1
    session.flush()
    return inserted


def seed_projects(session: Session) -> int:
    inserted = 0
    for p in PROJECTS:
        if session.get(ProjectORM, p["project_id"]) is not None:
            continue
        session.add(ProjectORM(**p))
        inserted += 1
    session.flush()
    return inserted


def seed_api_keys(session: Session) -> list[tuple[str, str, str]]:
    """One API key per project. Returns [(project_name, key_prefix, raw_key)]."""
    out: list[tuple[str, str, str]] = []
    svc = ApiKeyService()
    for p in PROJECTS:
        proj = session.get(ProjectORM, p["project_id"])
        if proj is None:
            continue
        # Skip if an active key already exists for this project (idempotent).
        existing = session.execute(
            sa.select(ApiKeyORM).where(
                ApiKeyORM.project_id == proj.project_id,
                ApiKeyORM.status == "active",
            ).limit(1)
        ).scalar_one_or_none()
        if existing is not None:
            out.append((proj.name, existing.key_prefix, "<existing — not shown>"))
            continue
        raw_key, row = svc.create_key(
            session,
            tenant_id=proj.tenant_id,
            project_id=proj.project_id,
            name=f"{proj.name} — UAT Key",
        )
        out.append((proj.name, row.key_prefix, raw_key))
    return out


def seed_portal_users(session: Session) -> int:
    inserted = 0
    pw_hash = _hash_password(PORTAL_USER_PASSWORD)
    for u in PORTAL_USERS:
        # Email is the natural unique key.
        existing = session.execute(
            sa.select(PortalUserORM.id).where(PortalUserORM.email == u["email"])
        ).scalar_one_or_none()
        if existing is not None:
            continue
        session.add(
            PortalUserORM(
                id=str(uuid.uuid4()),
                email=u["email"],
                password_hash=pw_hash,
                display_name=u["display_name"],
                tenant_id=u["tenant_id"],
                tenant_role=u["tenant_role"],
                credit_balance=u["credit_balance"],
                status="active",
                email_verified=True,
                preferred_language="th",
            )
        )
        inserted += 1
    session.flush()
    return inserted


def seed_policies(session: Session) -> int:
    inserted = 0
    for p in POLICY_PROFILES:
        if session.get(PolicyORM, p["profile_id"]) is not None:
            continue
        session.add(PolicyORM(**p))
        inserted += 1
    session.flush()
    return inserted


def main() -> int:
    session = SessionLocal()
    try:
        admin_created, admin_username = seed_admin(session)
        n_models = seed_model_configs(session)
        n_tenants = seed_tenants(session)
        n_projects = seed_projects(session)
        api_keys = seed_api_keys(session)
        n_users = seed_portal_users(session)
        n_policies = seed_policies(session)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    print()
    print("=" * 72)
    print("UAT SEED COMPLETE")
    print("=" * 72)
    print(f"  admin_user     : {'created' if admin_created else 'kept existing'} "
          f"({admin_username})")
    print(f"  model_configs  : +{n_models}  (target: {len(SEED_CANDIDATES)} candidates)")
    print(f"  tenants        : +{n_tenants} (target: {len(TENANTS)})")
    print(f"  projects       : +{n_projects} (target: {len(PROJECTS)})")
    print(f"  portal_users   : +{n_users} (target: {len(PORTAL_USERS)})")
    print(f"  policy_profiles: +{n_policies} (target: {len(POLICY_PROFILES)})")
    print()
    print("-" * 72)
    print("CREDENTIALS — copy into handoff doc, then RECORD AND DESTROY")
    print("-" * 72)
    print(f"Admin UI         : admin / {ADMIN_PASSWORD}")
    print()
    print("Portal users (all share the same password):")
    for u in PORTAL_USERS:
        print(f"  {u['email']:<35}  {PORTAL_USER_PASSWORD}   ({u['tenant_role']}/{u['tenant_id']})")
    print()
    print("API keys (each shown ONCE — store in customer's secret manager):")
    for project_name, prefix, raw in api_keys:
        print(f"  [{prefix}...]  {raw}   — {project_name}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
