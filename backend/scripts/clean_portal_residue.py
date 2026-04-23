"""Clean portal-user residue from manual testing.

The seeded portal users (alice / bob / charlie / dave / eve, from
``seed_audit_data.py``) live in the ``acme-001`` and ``globex-001``
tenants. Everything else under ``demo-tenant-000`` is typically
residue from portal signup / verification flows exercised during
live testing, and will otherwise survive across ``make seed`` runs
unless the DB volume is wiped with ``make clean``.

This script deletes everything in ``portal_users`` that is NOT one
of the five seeded emails. Safe to re-run — idempotent.

Usage:
    docker compose exec backend python -m scripts.clean_portal_residue
"""
from __future__ import annotations

import sys

from sqlalchemy import delete, select

sys.path.insert(0, ".")

from app.core.db import SessionLocal
from app.models.portal_user import PortalUserORM
import app.models  # noqa: F401

SEED_EMAILS = {
    "alice@acme.test",
    "bob@globex.test",
    "charlie@acme.test",
    "dave@acme.test",
    "eve@globex.test",
}


def main():
    session = SessionLocal()
    try:
        users = session.execute(select(PortalUserORM)).scalars().all()
        residue = [u for u in users if u.email not in SEED_EMAILS]
        if not residue:
            print(f"Clean: {len(users)} users, all seeded.")
            return
        print(f"Found {len(residue)} residue user(s); deleting:")
        for u in residue:
            print(f"  - {u.email} (tenant={u.tenant_id}, status={u.status})")
            session.execute(
                delete(PortalUserORM).where(PortalUserORM.id == u.id)
            )
        session.commit()
        remaining = session.execute(select(PortalUserORM)).scalars().all()
        print(f"Done. {len(remaining)} portal user(s) remain:")
        for u in remaining:
            print(f"  - {u.email}")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
