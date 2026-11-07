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
            details=json.dumps(details, ensure_ascii=False) if details else None,
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

    def list_with_filters(
        self,
        session: Session,
        *,
        tenant_id: str | None = None,
        actor_key_id: str | None = None,
        action: str | None = None,
        outcome: str | None = None,
        q: str | None = None,
        date_from=None,
        date_to=None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AdminAuditLogORM], int]:
        """Phase 4 F4 — filtered + paginated audit log query.

        ``tenant_id=None`` returns cross-tenant rows (super-admin view).
        ``q`` matches resource_id / resource_type / action substrings
        via ILIKE — fine for internal-tool scale, a trigram index is
        post-launch work if audit volume warrants it.

        Returns ``(rows, total)`` so the caller can render pagination
        without a second round-trip.
        """
        from sqlalchemy import func, or_

        base = select(AdminAuditLogORM)
        count_base = select(func.count(AdminAuditLogORM.id))

        filters = []
        if tenant_id is not None:
            filters.append(AdminAuditLogORM.tenant_id == tenant_id)
        if actor_key_id is not None:
            filters.append(AdminAuditLogORM.actor_key_id == actor_key_id)
        if action is not None:
            filters.append(AdminAuditLogORM.action == action)
        if outcome is not None:
            filters.append(AdminAuditLogORM.outcome == outcome)
        if date_from is not None:
            filters.append(AdminAuditLogORM.timestamp >= date_from)
        if date_to is not None:
            filters.append(AdminAuditLogORM.timestamp <= date_to)
        if q:
            pattern = f"%{q}%"
            filters.append(
                or_(
                    AdminAuditLogORM.resource_id.ilike(pattern),
                    AdminAuditLogORM.resource_type.ilike(pattern),
                    AdminAuditLogORM.action.ilike(pattern),
                )
            )

        if filters:
            base = base.where(*filters)
            count_base = count_base.where(*filters)

        total = int(session.execute(count_base).scalar() or 0)
        rows = list(
            session.execute(
                base.order_by(AdminAuditLogORM.timestamp.desc())
                .limit(limit)
                .offset(offset)
            )
            .scalars()
            .all()
        )
        return rows, total
