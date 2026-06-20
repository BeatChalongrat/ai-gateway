"""Portal credit ledger.

All balance mutations go through this module. Two design rules:

1. Every balance change MUST write a ``credit_transactions`` row so the
   admin can reconstruct "why was this user's balance X at time T?"
   without replaying the gateway request log.
2. Deductions use ``SELECT ... FOR UPDATE`` so concurrent gateway
   requests for the same user can't both read the pre-deduction
   balance and double-spend the delta.
"""

from __future__ import annotations

import uuid
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.credit_transaction import CreditTransactionORM
from app.models.portal_user import PortalUserORM
from app.models.system_setting import SystemSettingORM


TransactionType = Literal[
    "signup_bonus",
    "usage",
    "manual_topup",
    "purchase",
    "adjustment",
]


def credits_for_cost(cost_usd: float, markup: float = 1.0) -> int:
    """Convert a request's real USD cost into ledger credits.

    THE single conversion used by both the usage DISPLAY (portal usage
    summary) and the balance DEDUCTION, so the panel always matches what
    actually left the balance:

        credits = ceil(cost_usd * markup * settings.usage_credits_per_usd)

    Rounded UP (never give away fractional credits) with a floor of
    1 credit per billable request — a near-zero-cost call still costs
    something. ``markup`` is the tenant's ``credit_markup`` multiplier
    (1.0 = at cost).
    """
    import math

    from app.core.config import settings

    raw = float(cost_usd or 0.0) * max(1.0, float(markup or 1.0))
    return max(1, math.ceil(raw * int(settings.usage_credits_per_usd)))


def get_setting(db: Session, key: str, default: str | None = None) -> str | None:
    """Read a value from ``system_settings``.

    Returns ``default`` if the key is missing. Callers that require the
    setting (e.g. ``free_trial_tokens``) should assert or supply a
    sensible fallback themselves — this function stays neutral.
    """
    row = db.get(SystemSettingORM, key)
    if row is None:
        return default
    return row.value


def _lock_user(db: Session, user_id: str) -> PortalUserORM | None:
    """Load a portal user row with ``FOR UPDATE`` so the calling
    transaction sees a consistent balance for the duration of the
    deduction / topup.
    """
    stmt = (
        select(PortalUserORM)
        .where(PortalUserORM.id == user_id)
        .with_for_update()
    )
    return db.execute(stmt).scalar_one_or_none()


def deduct_credits(
    db: Session,
    user_id: str,
    tokens_used: int,
    request_id: str | None = None,
) -> CreditTransactionORM | None:
    """Deduct ``tokens_used`` from the user's balance.

    Behavior:
    - Clamps at 0 — the balance never goes negative even if the caller
      over-reports usage (protects against a runaway adapter).
    - ``credit_used`` is incremented by the *actual* amount deducted
      (i.e. after clamping), so totals stay consistent with the ledger.
    - Writes a ``credit_transactions`` row of type ``usage`` with a
      snapshot of the post-deduction balance. Returns that row.
    - If ``tokens_used <= 0`` this is a no-op and returns ``None`` —
      don't pollute the ledger with zero-amount rows.
    """
    if tokens_used <= 0:
        return None

    user = _lock_user(db, user_id)
    if user is None:
        return None

    actual = min(tokens_used, int(user.credit_balance))
    user.credit_balance = int(user.credit_balance) - actual
    user.credit_used = int(user.credit_used) + actual
    db.flush()

    tx = CreditTransactionORM(
        id=str(uuid.uuid4()),
        user_id=user.id,
        amount=-actual,
        balance_after=int(user.credit_balance),
        type="usage",
        description=None,
        request_id=request_id,
        created_by=None,
    )
    db.add(tx)
    db.flush()
    return tx


def topup_credits(
    db: Session,
    user_id: str,
    amount: int,
    created_by: str,
    description: str | None = None,
    *,
    type: TransactionType = "manual_topup",
) -> CreditTransactionORM | None:
    """Credit ``amount`` tokens to the user's balance.

    Used by admin top-up (``type=manual_topup``), the billing purchase
    flow (``type=purchase``), and the signup bonus (``type=signup_bonus``).
    Returns the transaction row, or ``None`` if the user doesn't exist.
    """
    if amount <= 0:
        return None

    user = _lock_user(db, user_id)
    if user is None:
        return None

    user.credit_balance = int(user.credit_balance) + amount
    db.flush()

    tx = CreditTransactionORM(
        id=str(uuid.uuid4()),
        user_id=user.id,
        amount=amount,
        balance_after=int(user.credit_balance),
        type=type,
        description=description,
        request_id=None,
        created_by=created_by,
    )
    db.add(tx)
    db.flush()
    return tx
