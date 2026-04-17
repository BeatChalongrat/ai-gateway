"""Seed demo data for development and testing.

Creates:
- Demo tenant (slug: demo)
- Demo project (policy_profile: default)
- Demo API key (printed to stdout — shown once)

Idempotent: re-running skips existing records.

Usage:
    cd backend
    python -m scripts.seed_demo_data
"""

from __future__ import annotations

import sys
import uuid

from sqlalchemy import select

# Ensure app modules are importable
sys.path.insert(0, ".")

from app.core.db import SessionLocal
from app.models.tenant import TenantORM
from app.models.project import ProjectORM
from app.models.api_key import ApiKeyORM
from app.services.api_key_service import ApiKeyService
import app.models  # noqa: F401

DEMO_TENANT_ID = "demo-tenant-000"
DEMO_TENANT_SLUG = "demo"
DEMO_PROJECT_ID = "demo-project-000"
DEMO_KEY_NAME = "Demo Key"


def seed(session) -> dict:
    """Run the seed logic. Returns a summary dict."""
    result = {"tenant_created": False, "project_created": False, "key_created": False}

    # 1. Tenant
    tenant = session.get(TenantORM, DEMO_TENANT_ID)
    if tenant is None:
        tenant = TenantORM(
            tenant_id=DEMO_TENANT_ID,
            name="Demo Tenant",
            slug=DEMO_TENANT_SLUG,
        )
        session.add(tenant)
        session.flush()
        result["tenant_created"] = True

    # 2. Project
    project = session.get(ProjectORM, DEMO_PROJECT_ID)
    if project is None:
        project = ProjectORM(
            project_id=DEMO_PROJECT_ID,
            tenant_id=DEMO_TENANT_ID,
            name="Demo Project",
            policy_profile_id="default",
        )
        session.add(project)
        session.flush()
        result["project_created"] = True

    # 3. API Key (only create if none exists for this project)
    existing_key = session.execute(
        select(ApiKeyORM).where(
            ApiKeyORM.project_id == DEMO_PROJECT_ID,
            ApiKeyORM.status == "active",
        )
    ).scalar_one_or_none()

    if existing_key is None:
        svc = ApiKeyService()
        raw_key, api_key = svc.create_key(
            session,
            tenant_id=DEMO_TENANT_ID,
            project_id=DEMO_PROJECT_ID,
            name=DEMO_KEY_NAME,
        )
        result["key_created"] = True
        result["raw_key"] = raw_key
    else:
        result["raw_key"] = None  # Already exists, can't show raw key again

    return result


def main():
    session = SessionLocal()
    try:
        result = seed(session)
        session.commit()

        if result["tenant_created"]:
            print(f"Created tenant: {DEMO_TENANT_ID} (slug: {DEMO_TENANT_SLUG})")
        else:
            print(f"Tenant already exists: {DEMO_TENANT_ID}")

        if result["project_created"]:
            print(f"Created project: {DEMO_PROJECT_ID}")
        else:
            print(f"Project already exists: {DEMO_PROJECT_ID}")

        if result["key_created"]:
            print(f"Created API key: {result['raw_key']}")
            print("  ^ Save this key — it will not be shown again.")
        else:
            print("Active API key already exists for demo project.")

    except Exception as e:
        session.rollback()
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        session.close()


if __name__ == "__main__":
    main()
