"""Internal admin-audit writer (AM7) — chat-edge's door into ``admin_audit_log``.

Agent Market's settings (who may use it, budgets, whether documents may go to
a model outside the organisation, retention, the catalog, the problems queue,
openings of someone's run, the mail relay) live in chat-edge's database. The
admin audit log auditors read (admin-ui /audit-log) lives here. chat-edge
writes every admin change it makes to that log through this endpoint BEFORE
the change lands, and makes no change the log did not take.

Security contract:
  * chat-edge only — the caller must present chat-edge's short-lived service
    JWT (iss=chat-edge-api, aud=gateway-api, SERVICE_JWT_SECRET). An API key,
    even with the internal-service secret, is not enough: nothing else in the
    platform writes Agent Market's lines.
  * It can write Agent Market's lines and nothing else: the action must be
    ``agent_market.*`` and the actor an admin-ui session (``session:<id>``) or
    a chat user (``chat-user:<uuid>``) — never ``system:``, ``jwt:`` or a key.
  * A batch is written in one transaction, all or nothing, and committed
    before the 201 goes back (chat-edge commits its change on that answer).
"""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.api.deps import ResolvedApiKey, _resolve_service_jwt, get_db_session
from app.core.exceptions import AuthenticationError
from app.services.admin_audit_service import AdminAuditService

router = APIRouter(prefix="/internal/admin-audit", tags=["internal-admin-audit"])

_audit = AdminAuditService()

# One entry's details, as JSON text, at most this long.
MAX_DETAILS_BYTES = 16_384


def require_chat_edge_service(
    authorization: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
) -> ResolvedApiKey:
    """The caller is chat-edge, proven by its service JWT — nothing else."""
    raw = (authorization or "").removeprefix("Bearer ").strip() if (authorization or "").startswith("Bearer ") else ""
    svc = _resolve_service_jwt(raw, x_user_id=x_user_id, x_tenant_id=x_tenant_id, trusted_conversation_id=None) if raw else None
    if svc is None:
        raise AuthenticationError("chat-edge service token required")
    return svc


class AuditEntryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_key_id: str = Field(min_length=1, max_length=100, pattern=r"^(session|chat-user):[A-Za-z0-9._-]{1,64}$")
    tenant_id: str = Field(min_length=1, max_length=36)
    action: str = Field(min_length=14, max_length=100, pattern=r"^agent_market\.[a-z0-9_.]+$")
    resource_type: str = Field(min_length=1, max_length=50, pattern=r"^agent_market_[a-z0-9_]+$")
    resource_id: str = Field(min_length=1, max_length=36)
    details: dict[str, Any] | None = None
    outcome: Literal["success", "failed"] = "success"

    @field_validator("details")
    @classmethod
    def _small(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is not None and len(json.dumps(v, ensure_ascii=False).encode()) > MAX_DETAILS_BYTES:
            raise ValueError("details too large")
        return v


class AuditBatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: list[AuditEntryIn] = Field(min_length=1, max_length=50)


@router.post("", status_code=201)
def record_admin_changes(
    body: AuditBatchIn,
    _svc: ResolvedApiKey = Depends(require_chat_edge_service),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    ids: list[int] = []
    for e in body.entries:
        row = _audit.record(
            db,
            actor_key_id=e.actor_key_id,
            tenant_id=e.tenant_id,
            action=e.action,
            resource_type=e.resource_type,
            resource_id=e.resource_id,
            details=e.details,
        )
        row.outcome = e.outcome
        ids.append(row.id)
    db.commit()  # durable before chat-edge is told, and commits its change
    return {"recorded": len(ids), "ids": ids}
