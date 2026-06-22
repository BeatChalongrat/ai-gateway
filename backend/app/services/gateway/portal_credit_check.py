"""Gateway-side credit check + deduction for portal users.

Two hooks in the request lifecycle:

- ``check_portal_credit(...)`` runs AFTER rate-limit / quota and BEFORE
  DLP. It rejects the request when the billable portal user is
  suspended (403) or out of credits (insufficient_credits). No-op when
  no billable user resolves.

- ``deduct_portal_credit(...)`` runs AFTER the provider response lands.
  It converts the request's REAL USD cost into ledger credits via
  ``credit_service.credits_for_cost`` (the same conversion the usage
  panel displays), applies the tenant's ``credit_markup``, writes a
  ``credit_transactions`` row of type=usage and atomically decrements
  the balance (clamping at 0). Minimum 1 credit per billable request;
  rounded UP so fractional credits are never given away. Raw token
  counts stay on ``request_logs`` for analytics — the ledger is now
  cost-based.

Billable-user resolution (first match wins):

1. The key's owning portal user (``api_keys.portal_user_id``) — direct
   portal API-key traffic.
2. The TRUSTED end-user behind a shared service key
   (``ResolvedApiKey.portal_user_id``, i.e. ``X-User-ID`` proven by the
   internal-service secret) — web-chat traffic, so the actual chatting
   user's balance drains.

Both hooks take the raw ``api_key_id`` plus the optional trusted
``portal_user_id`` rather than loaded ORM objects.
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
from app.models.tenant import TenantORM
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


def _resolve_billable_user(
    session: Session, key_id: str, portal_user_id: str | None
) -> PortalUserORM | None:
    """Key-owner first, trusted end-user second (see module docstring)."""
    user = _load_portal_user_for_key(session, key_id)
    if user is not None:
        return user
    if portal_user_id:
        return session.get(PortalUserORM, portal_user_id)
    return None


def _tenant_markup(session: Session, tenant_id: str | None) -> float:
    """The tenant's credit_markup multiplier; 1.0 when unset/unknown."""
    if not tenant_id:
        return 1.0
    tenant = session.get(TenantORM, tenant_id)
    if tenant is None:
        return 1.0
    try:
        return max(1.0, float(tenant.credit_markup or 1.0))
    except (TypeError, ValueError):
        return 1.0


def _raise_for_user_state(user: PortalUserORM) -> None:
    if user.status != "active":
        raise AuthorizationError(
            "Account suspended. Contact support.",
            code="account_suspended",
            details={"status": user.status},
        )
    if int(user.credit_balance) <= 0:
        # QuotaExceededError so the existing exception handler emits a
        # clean JSON body. Clients should branch on the ``code`` field,
        # not the HTTP status alone — ``code=insufficient_credits`` is
        # unique to this condition.
        raise QuotaExceededError(
            "No credits remaining. Please contact your administrator "
            "to add more credits.",
            code="insufficient_credits",
            details={"credit_balance": 0, "upgrade_url": "/billing"},
        )


def check_portal_credit(
    session: Session, key_id: str, portal_user_id: str | None = None
) -> None:
    """Gate a billable request before expensive DLP/provider work.

    Raises:
        AuthorizationError (403, code=account_suspended): owner is
            suspended or deleted.
        QuotaExceededError (code=insufficient_credits): balance has
            already hit zero.
    """
    user = _resolve_billable_user(session, key_id, portal_user_id)
    if user is None:
        return  # no billable identity — nothing to do
    _raise_for_user_state(user)


def deduct_portal_credit(
    session: Session,
    key_id: str,
    request_id: str,
    *,
    cost_usd: float,
    tokens_used: int = 0,
    tenant_id: str | None = None,
    portal_user_id: str | None = None,
) -> None:
    """Hook called after a successful provider response.

    Cost-based: ``credits = ceil(cost_usd * tenant_markup *
    USAGE_CREDITS_PER_USD)``, minimum 1 per billable request. Silently
    no-ops when no billable user resolves, or when the request did no
    billable work at all (zero cost AND zero tokens — e.g. blocked
    pre-provider).
    """
    if float(cost_usd or 0.0) <= 0.0 and int(tokens_used or 0) <= 0:
        return
    user = _resolve_billable_user(session, key_id, portal_user_id)
    if user is None:
        return
    markup = _tenant_markup(session, tenant_id)
    credits = credit_service.credits_for_cost(cost_usd, markup)
    credit_service.deduct_credits(
        session,
        user_id=user.id,
        tokens_used=credits,
        request_id=request_id,
    )


# ----------------------------------------------------------------------
# Async variants (Track A) — additive; sync paths above unchanged.
# ----------------------------------------------------------------------


async def _load_portal_user_for_key_async(
    session: AsyncSession, key_id: str
) -> PortalUserORM | None:
    """Async mirror of :func:`_load_portal_user_for_key`."""
    if not key_id or key_id.startswith("jwt:"):
        return None
    key = await session.get(ApiKeyORM, key_id)
    if key is None or key.portal_user_id is None:
        return None
    return await session.get(PortalUserORM, key.portal_user_id)


async def _resolve_billable_user_async(
    session: AsyncSession, key_id: str, portal_user_id: str | None
) -> PortalUserORM | None:
    user = await _load_portal_user_for_key_async(session, key_id)
    if user is not None:
        return user
    if portal_user_id:
        return await session.get(PortalUserORM, portal_user_id)
    return None


async def _tenant_markup_async(
    session: AsyncSession, tenant_id: str | None
) -> float:
    if not tenant_id:
        return 1.0
    tenant = await session.get(TenantORM, tenant_id)
    if tenant is None:
        return 1.0
    try:
        return max(1.0, float(tenant.credit_markup or 1.0))
    except (TypeError, ValueError):
        return 1.0


async def check_portal_credit_async(
    session: AsyncSession, key_id: str, portal_user_id: str | None = None
) -> None:
    """Async variant of :func:`check_portal_credit`."""
    user = await _resolve_billable_user_async(session, key_id, portal_user_id)
    if user is None:
        return  # no billable identity — nothing to do
    _raise_for_user_state(user)


async def deduct_portal_credit_async(
    session: AsyncSession,
    key_id: str,
    request_id: str,
    *,
    cost_usd: float = 0.0,
    tokens_used: int = 0,
    tenant_id: str | None = None,
    portal_user_id: str | None = None,
    credits_override: int | None = None,
    description: str | None = None,
) -> None:
    """Async variant of :func:`deduct_portal_credit`.

    Mirrors ``credit_service.deduct_credits`` semantics on an
    ``AsyncSession``: row-level ``SELECT ... FOR UPDATE`` lock on the
    user, a clamped-at-zero balance decrement, ``credit_used``
    incremented by the actually-deducted amount, and a
    ``credit_transactions`` row of type ``usage`` snapshotting the
    post-deduction balance.

    Deduction amount is cost-based by default. ``credits_override`` sets
    a FLAT charge instead (ignoring cost/markup) — used by web search,
    which bills a fixed ``search_credit_cost`` per query.
    """
    if credits_override is not None:
        credits = max(0, int(credits_override))
        if credits <= 0:
            return
    else:
        if float(cost_usd or 0.0) <= 0.0 and int(tokens_used or 0) <= 0:
            return
    user = await _resolve_billable_user_async(session, key_id, portal_user_id)
    if user is None:
        return
    if credits_override is None:
        markup = await _tenant_markup_async(session, tenant_id)
        credits = credit_service.credits_for_cost(cost_usd, markup)

    locked = (
        await session.execute(
            select(PortalUserORM)
            .where(PortalUserORM.id == user.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if locked is None:
        return

    actual = min(int(credits), int(locked.credit_balance))
    locked.credit_balance = int(locked.credit_balance) - actual
    locked.credit_used = int(locked.credit_used) + actual
    await session.flush()

    tx = CreditTransactionORM(
        id=str(uuid.uuid4()),
        user_id=locked.id,
        amount=-actual,
        balance_after=int(locked.credit_balance),
        type="usage",
        description=description,
        request_id=request_id,
        created_by=None,
    )
    session.add(tx)
    await session.flush()


# ---------------------------------------------------------------------------
# Media-generation cost pre-check (Sprint 1).
# ---------------------------------------------------------------------------

from dataclasses import dataclass as _dataclass  # noqa: E402
from decimal import Decimal as _Decimal  # noqa: E402


@_dataclass
class EstimateResult:
    """Result of a media-gen cost estimate + balance check (no deduction)."""

    estimated_credits: int
    balance: int
    sufficient: bool
    breakdown: dict


def _media_cost_usd(job_type: str, model_config, input_metadata: dict) -> _Decimal:
    """Pure USD cost estimate. Raises ValueError if the required cost field
    is missing on the model_config or the job_type is unknown.

    Reads cost fields via ``getattr`` because the new model_configs cost
    columns (migration 056) are not yet declared on the ORM model — that
    wiring lands in a later sprint.
    """
    meta = input_metadata or {}
    if job_type == "video":
        rate = getattr(model_config, "cost_per_second", None)
        if rate is None:
            raise ValueError("model_config.cost_per_second is required for video")
        units = _Decimal(str(meta.get("duration_sec", 0) or 0))
        return _Decimal(str(rate)) * units
    if job_type == "audio_tts":
        rate = getattr(model_config, "cost_per_character", None)
        if rate is None:
            raise ValueError(
                "model_config.cost_per_character is required for audio_tts"
            )
        units = _Decimal(str(meta.get("char_count", 0) or 0))
        return _Decimal(str(rate)) * units
    if job_type == "music":
        rate = getattr(model_config, "cost_per_generation", None)
        if rate is None:
            raise ValueError(
                "model_config.cost_per_generation is required for music"
            )
        return _Decimal(str(rate))
    raise ValueError(f"unsupported media job_type: {job_type!r}")


async def estimate_and_check_balance(
    session: AsyncSession,
    tenant_id: str | None,
    user_id: str | None,
    job_type: str,
    model_config,
    input_metadata: dict,
) -> EstimateResult:
    """Estimate a media-gen job's credit cost and check the user's balance.

    Does NOT deduct. Reuses ``credit_service.credits_for_cost`` (no
    duplicated rounding/markup math) and the tenant's ``credit_markup``.

    NOTE: extends the Sprint plan's 4-arg signature with ``session`` and
    ``user_id`` — both are required to read the tenant markup and the
    user's balance, which the plan's "fetch current balance" step assumed
    but did not pass in.
    """
    cost_usd = _media_cost_usd(job_type, model_config, input_metadata)
    markup = await _tenant_markup_async(session, tenant_id)
    estimated_credits = credit_service.credits_for_cost(float(cost_usd), markup)

    balance = 0
    if user_id:
        user = await session.get(PortalUserORM, user_id)
        if user is not None:
            balance = int(user.credit_balance or 0)

    return EstimateResult(
        estimated_credits=estimated_credits,
        balance=balance,
        sufficient=balance >= estimated_credits,
        breakdown={
            "job_type": job_type,
            "cost_usd": float(cost_usd),
            "markup": markup,
        },
    )
