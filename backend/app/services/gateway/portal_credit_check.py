"""Gateway-side credit check for portal-owned API keys.

Two hooks in the orchestrator:

- ``check_portal_credit(...)`` runs AFTER rate-limit / quota and BEFORE
  DLP. It fast-paths (no-op) for admin or tenant-scoped keys that have
  no ``portal_user_id``. For portal-owned keys it rejects the request
  if the owning user is suspended (403) or out of credits (402).

- ``deduct_portal_credit(...)`` runs AFTER the provider response lands
  and ``usage.total_tokens`` is known. It's a no-op for non-portal
  keys; for portal keys it writes a ``credit_transactions`` row of
  type=usage and atomically decrements the balance (clamping at 0).

Both functions take the raw ``api_key_id`` (a.k.a. ``key_id`` on
``ResolvedApiKey``) rather than a loaded ORM object — the orchestrator
passes whatever it has, and we resolve to a ``PortalUserORM`` internally.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.exceptions import AuthorizationError, QuotaExceededError
from app.models.api_key import ApiKeyORM
from app.models.credit_transaction import CreditTransactionORM
from app.models.portal_user import PortalUserORM
from app.services.portal import credit_service


def _load_portal_user_for_key(
    session: Session, key_id: str
) -> PortalUserORM | None:
    """Return the portal user that owns ``key_id`` or ``None`` if
    either the key isn't in ``api_keys`` (e.g. JWT admin identity) or
    the key exists but is tenant/admin-scoped (portal_user_id is NULL).
    """
    # JWT-style key_id like "jwt:<uuid>" never exists in api_keys.
    if not key_id or key_id.startswith("jwt:"):
        return None
    key = session.get(ApiKeyORM, key_id)
    if key is None or key.portal_user_id is None:
        return None
    return session.get(PortalUserORM, key.portal_user_id)


def check_portal_credit(session: Session, key_id: str) -> None:
    """Gate a portal-owned request before expensive DLP/provider work.

    Raises:
        AuthorizationError (403, code=account_suspended): owner is
            suspended or deleted.
        QuotaExceededError (429, code=insufficient_credits): balance
            has already hit zero.
    """
    user = _load_portal_user_for_key(session, key_id)
    if user is None:
        return  # non-portal key — nothing to do
    if user.status != "active":
        raise AuthorizationError(
            "Account suspended. Contact support.",
            code="account_suspended",
            details={"status": user.status},
        )
    if int(user.credit_balance) <= 0:
        # Use QuotaExceededError so the existing exception handler emits
        # a clean JSON body and 429 status. Clients should branch on the
        # ``code`` field, not the HTTP status alone — 429 is already used
        # for rate limit, but ``code=insufficient_credits`` is unique.
        raise QuotaExceededError(
            "No credits remaining. Please upgrade your plan.",
            code="insufficient_credits",
            details={"credit_balance": 0, "upgrade_url": "/billing"},
        )


def deduct_portal_credit(
    session: Session, key_id: str, tokens_used: int, request_id: str
) -> None:
    """Hook called after a successful provider response.

    Silently no-op for non-portal keys so admin and tenant traffic
    doesn't go through the ledger. Uses ``credit_service.deduct_credits``
    which takes a row-level lock and clamps at 0.
    """
    if tokens_used <= 0:
        return
    user = _load_portal_user_for_key(session, key_id)
    if user is None:
        return
    credit_service.deduct_credits(
        session,
        user_id=user.id,
        tokens_used=int(tokens_used),
        request_id=request_id,
    )


# ----------------------------------------------------------------------
# Async variants (Track A) — additive; sync paths above unchanged.
# ----------------------------------------------------------------------


async def _load_portal_user_for_key_async(
    session: AsyncSession, key_id: str
) -> PortalUserORM | None:
    """Async mirror of :func:`_load_portal_user_for_key`.

    Same short-circuits: empty / ``jwt:``-prefixed key_ids and
    tenant/admin-scoped keys (``portal_user_id is None``) return ``None``.
    Only the two ``session.get`` reads are awaited.
    """
    if not key_id or key_id.startswith("jwt:"):
        return None
    key = await session.get(ApiKeyORM, key_id)
    if key is None or key.portal_user_id is None:
        return None
    return await session.get(PortalUserORM, key.portal_user_id)


async def check_portal_credit_async(
    session: AsyncSession, key_id: str
) -> None:
    """Async variant of :func:`check_portal_credit`.

    Identical gating: no-op for non-portal keys, 403 (account_suspended)
    for non-active owners, 429 (insufficient_credits) when the balance is
    at or below zero.
    """
    user = await _load_portal_user_for_key_async(session, key_id)
    if user is None:
        return  # non-portal key — nothing to do
    if user.status != "active":
        raise AuthorizationError(
            "Account suspended. Contact support.",
            code="account_suspended",
            details={"status": user.status},
        )
    if int(user.credit_balance) <= 0:
        raise QuotaExceededError(
            "No credits remaining. Please upgrade your plan.",
            code="insufficient_credits",
            details={"credit_balance": 0, "upgrade_url": "/billing"},
        )


async def deduct_portal_credit_async(
    session: AsyncSession, key_id: str, tokens_used: int, request_id: str
) -> None:
    """Async variant of :func:`deduct_portal_credit`.

    Mirrors ``credit_service.deduct_credits`` semantics on an
    ``AsyncSession``: silent no-op for non-portal keys and non-positive
    token counts; otherwise a row-level ``SELECT ... FOR UPDATE`` lock on
    the user, a clamped-at-zero balance decrement (so it never goes
    negative even on over-reported usage), ``credit_used`` incremented by
    the actually-deducted amount, and a ``credit_transactions`` row of
    type ``usage`` snapshotting the post-deduction balance.

    The async deduction is inlined here (rather than calling the sync
    ``credit_service.deduct_credits``) because that helper opens a sync
    ``SELECT ... FOR UPDATE`` that cannot share an ``AsyncSession``; the
    behaviour is kept identical.
    """
    if tokens_used <= 0:
        return
    user = await _load_portal_user_for_key_async(session, key_id)
    if user is None:
        return

    locked = (
        await session.execute(
            select(PortalUserORM)
            .where(PortalUserORM.id == user.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if locked is None:
        return

    actual = min(int(tokens_used), int(locked.credit_balance))
    locked.credit_balance = int(locked.credit_balance) - actual
    locked.credit_used = int(locked.credit_used) + actual
    await session.flush()

    tx = CreditTransactionORM(
        id=str(uuid.uuid4()),
        user_id=locked.id,
        amount=-actual,
        balance_after=int(locked.credit_balance),
        type="usage",
        description=None,
        request_id=request_id,
        created_by=None,
    )
    session.add(tx)
    await session.flush()
