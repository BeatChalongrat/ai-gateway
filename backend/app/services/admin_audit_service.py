"""Admin audit trail service.

Records sensitive admin operations for compliance and debugging.
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.admin_audit_log import AdminAuditLogORM


class AdminAuditService:

    def record(
        self,
        session: Session,
        *,
        actor_key_id: str,
        tenant_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        details: dict | None = None,
    ) -> AdminAuditLogORM:
        entry = AdminAuditLogORM(
            actor_key_id=actor_key_id,
            tenant_id=tenant_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=json.dumps(details) if details else None,
        )
        session.add(entry)
        session.flush()
        return entry

    def list_by_tenant(
        self, session: Session, tenant_id: str, limit: int = 100
    ) -> list[AdminAuditLogORM]:
        stmt = (
            select(AdminAuditLogORM)
            .where(AdminAuditLogORM.tenant_id == tenant_id)
            .order_by(AdminAuditLogORM.timestamp.desc())
            .limit(limit)
        )
        return list(session.execute(stmt).scalars().all())

    def list_by_action(
        self, session: Session, action: str, limit: int = 100
    ) -> list[AdminAuditLogORM]:
        stmt = (
            select(AdminAuditLogORM)
            .where(AdminAuditLogORM.action == action)
            .order_by(AdminAuditLogORM.timestamp.desc())
            .limit(limit)
        )
        return list(session.execute(stmt).scalars().all())
