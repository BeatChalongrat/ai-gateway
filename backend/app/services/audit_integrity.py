"""Audit integrity service — tamper-evidence via hash chaining.

Each audit entry's hash includes the previous entry's hash, forming a
chain. If any entry is modified or deleted, the chain breaks.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models.admin_audit_log import AdminAuditLogORM


def _compute_hash(entry_id: int, action: str, resource_id: str, prev_hash: str) -> str:
    """Compute SHA-256 hash of an audit entry chained to the previous hash."""
    data = f"{entry_id}|{action}|{resource_id}|{prev_hash}"
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


GENESIS_HASH = "0" * 64  # Hash of the "genesis" block


class AuditIntegrityService:

    def compute_chain_hash(self, session: Session, entry: AdminAuditLogORM) -> str:
        """Compute the expected hash for an entry based on its predecessor."""
        prev_entry = session.execute(
            select(AdminAuditLogORM)
            .where(AdminAuditLogORM.id < entry.id)
            .order_by(AdminAuditLogORM.id.desc())
            .limit(1)
        ).scalar_one_or_none()

        prev_hash = GENESIS_HASH
        if prev_entry and prev_entry.details:
            try:
                details = json.loads(prev_entry.details)
                prev_hash = details.get("_chain_hash", GENESIS_HASH)
            except (json.JSONDecodeError, TypeError):
                pass

        return _compute_hash(entry.id, entry.action, entry.resource_id, prev_hash)

    def verify_chain(self, session: Session, limit: int = 100) -> dict:
        """Verify the integrity of the audit log chain.

        Returns {"valid": bool, "checked": int, "broken_at": int | None}.
        """
        entries = session.execute(
            select(AdminAuditLogORM)
            .order_by(AdminAuditLogORM.id.asc())
            .limit(limit)
        ).scalars().all()

        prev_hash = GENESIS_HASH
        for entry in entries:
            expected = _compute_hash(entry.id, entry.action, entry.resource_id, prev_hash)
            stored_hash = None
            if entry.details:
                try:
                    details = json.loads(entry.details)
                    stored_hash = details.get("_chain_hash")
                except (json.JSONDecodeError, TypeError):
                    pass

            if stored_hash is not None and stored_hash != expected:
                return {"valid": False, "checked": len(entries), "broken_at": entry.id}

            prev_hash = stored_hash or expected

        return {"valid": True, "checked": len(entries), "broken_at": None}
