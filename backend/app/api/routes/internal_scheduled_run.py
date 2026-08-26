"""Internal scheduled-run authorizer (FIX13) — trusted service-to-service.

chat-edge's cloud-task scheduler calls this BEFORE it fires any unattended
turn. It is the source of truth for the two conditions that must refuse an
unattended run:

  * ``revoked``     — the portal user is no longer active (departed /
                      suspended / deleted), so nothing may run on their
                      behalf. Same signal the gateway credit gate uses
                      (``portal_users.status != "active"``).
  * ``no_balance``  — the user's credit balance cannot cover a minimal turn
                      (``credit_balance <= 0``). Same read the gateway credit
                      gate uses; this endpoint only READS it, never mutates.

Security contract:
  * Internal-trusted only — the caller MUST prove ``X-Internal-Service-Secret``
    (``auth.internal_trusted``); a public/Direct-API caller gets 401. Same
    trust anchor as the media-job + DLP internal endpoints.
  * The (user_id, tenant_id) to check come from the BODY (the scheduler is
    acting for an arbitrary owner), NOT from the caller's own identity — but
    the caller must still be internally trusted to ask at all.
  * The response carries ONLY ``{allow, reason}`` — never a balance figure,
    a token, or any PII. A missing user, or a user in the wrong tenant, is
    indistinguishable from a revoked user (``revoked``).

THE ONE INVARIANT this backs: chat-edge must not run a task turn unless this
endpoint returned ``allow=true``. The decision here is deliberately a pure,
separately-tested function (``decide_scheduled_run``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ResolvedApiKey, get_resolved_api_key
from app.core.db import get_async_db
from app.models.portal_user import PortalUserORM

router = APIRouter(prefix="/internal/scheduled-run", tags=["internal-scheduled-run"])


class AuthorizeRequest(BaseModel):
    user_id: str
    tenant_id: str


class AuthorizeResponse(BaseModel):
    allow: bool
    # "ok" | "revoked" | "no_balance"
    reason: str


def _require_internal(auth: ResolvedApiKey) -> None:
    """Mirror the media-job / DLP internal gate: only a caller proving the
    internal-service secret (``auth.internal_trusted``) may ask."""
    if not getattr(auth, "internal_trusted", False):
        raise HTTPException(status_code=401, detail="internal service auth required")


def decide_scheduled_run(
    user: PortalUserORM | None, expected_tenant_id: str
) -> tuple[bool, str]:
    """Pure authorization decision — no I/O, unit-tested directly.

    Order matters: a non-active OR missing OR wrong-tenant user is ``revoked``
    (checked FIRST, so a departed user is refused even at zero balance); an
    active user with no balance is ``no_balance``; otherwise ``ok``.

    Reuses the SAME conditions the gateway credit gate enforces
    (``status != "active"`` and ``credit_balance <= 0``) — this only reads
    them.
    """
    if user is None:
        return False, "revoked"
    # A user resolved outside the tenant the scheduler expects is treated as
    # absent (never leak that the id exists elsewhere).
    if expected_tenant_id and user.tenant_id != expected_tenant_id:
        return False, "revoked"
    if user.status != "active":
        return False, "revoked"
    if int(user.credit_balance) <= 0:
        return False, "no_balance"
    return True, "ok"


@router.post("/authorize", response_model=AuthorizeResponse)
async def authorize_scheduled_run(
    payload: AuthorizeRequest,
    auth: ResolvedApiKey = Depends(get_resolved_api_key),
    session: AsyncSession = Depends(get_async_db),
) -> AuthorizeResponse:
    _require_internal(auth)

    user = await session.get(PortalUserORM, payload.user_id)
    allow, reason = decide_scheduled_run(user, payload.tenant_id)
    return AuthorizeResponse(allow=allow, reason=reason)
