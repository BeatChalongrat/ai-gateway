"""Portal credit ledger.

All balance mutations go through this module. Three design rules:

1. Every balance change MUST write a ``credit_transactions`` row so the
   admin can reconstruct "why was this user's balance X at time T?"
   without replaying the gateway request log. The ledger is immutable:
   a correction is a new row, never an edit.
2. Deductions use ``SELECT ... FOR UPDATE`` so concurrent gateway
   requests for the same user can't both read the pre-deduction
   balance and double-spend the delta. Every other mutation locks the
   same way.
3. FIX88 — the balance has a ceiling, ``portal_users.credit_quota``:

   - a manual top-up fills TOWARDS the ceiling and is refused
     (``CreditCeilingExceeded``, code ``credit_ceiling``) when it would
     go above it;
   - ``fill_to_quota`` sets the balance TO the ceiling;
   - a GRANT (signup bonus, purchase) raises the ceiling and the balance
     by the same amount — buying 500k means the ceiling grows 500k;
   - ``set_quota`` moves the ceiling and the balance by the same delta
     (``balance += new − old``, floored at 0);
   - usage never touches the ceiling.

   The invariant every function preserves: ``0 <= balance <= quota``.
"""

from __future__ import annotations

import uuid
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import ValidationError
from app.models.credit_transaction import CreditTransactionORM
from app.models.portal_user import PortalUserORM
from app.models.system_setting import SystemSettingORM


TransactionType = Literal[
    "signup_bonus",
    "usage",
    "manual_topup",
    "purchase",
    "adjustment",
    # FIX88 — "reset to full": balance := quota. Its own type so the
    # ledger reads as the operation the admin performed, not as an
    # anonymous adjustment.
    "reset_to_full",
]

# Types that RAISE the ceiling along with the balance (see module doc).
GRANT_TYPES: frozenset[str] = frozenset({"signup_bonus", "purchase"})


class CreditCeilingExceeded(ValidationError):
    """A top-up would lift the balance above the user's credit ceiling.

    Subclass of ``ValidationError`` so the HTTP layer answers 422 with
    ``code="credit_ceiling"``; ``details`` carries ``balance``,
    ``quota``, ``room`` (quota − balance) and the ``amount`` asked for
    so a UI can say exactly how much would still fit.
    """

    code = "credit_ceiling"
    message = "Top-up exceeds the user's credit ceiling."


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


def _ledger_row(
    db: Session,
    user: PortalUserORM,
    *,
    amount: int,
    type: str,
    description: str | None,
    created_by: str | None,
    request_id: str | None = None,
) -> CreditTransactionORM:
    """Append the ledger row for a balance movement that has ALREADY been
    applied to ``user`` (``balance_after`` snapshots the row as it is)."""
    tx = CreditTransactionORM(
        id=str(uuid.uuid4()),
        user_id=user.id,
        amount=int(amount),
        balance_after=int(user.credit_balance),
        type=type,
        description=description,
        request_id=request_id,
        created_by=created_by,
    )
    db.add(tx)
    db.flush()
    return tx


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
    - The ceiling (``credit_quota``) is untouched: usage lowers the
      balance, never the ceiling.
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

    return _ledger_row(
        db,
        user,
        amount=-actual,
        type="usage",
        description=None,
        created_by=None,
        request_id=request_id,
    )


def grant_credits(
    db: Session,
    user_id: str,
    amount: int,
    created_by: str,
    description: str | None = None,
    *,
    type: TransactionType = "purchase",
) -> CreditTransactionORM | None:
    """GRANT ``amount`` credits: ``balance += amount`` AND
    ``quota += amount`` in one locked write, plus a ledger row.

    The signup bonus and a purchase are grants — they are new
    entitlement, so the ceiling grows with them (buying 500k means the
    ceiling grows 500k). Returns the ledger row, or ``None`` when the
    amount is not positive or the user does not exist.
    """
    if amount <= 0:
        return None

    user = _lock_user(db, user_id)
    if user is None:
        return None

    user.credit_balance = int(user.credit_balance) + amount
    user.credit_quota = int(user.credit_quota) + amount
    db.flush()

    return _ledger_row(
        db,
        user,
        amount=amount,
        type=type,
        description=description,
        created_by=created_by,
    )


def topup_credits(
    db: Session,
    user_id: str,
    amount: int,
    created_by: str,
    description: str | None = None,
    *,
    type: TransactionType = "manual_topup",
    cap_at_quota: bool | None = None,
) -> CreditTransactionORM | None:
    """Credit ``amount`` tokens to the user's balance.

    Used by admin top-up (``type=manual_topup``), the billing purchase
    flow (``type=purchase``), and the signup bonus (``type=signup_bonus``).
    Returns the transaction row, or ``None`` if the user doesn't exist
    (or ``amount <= 0``).

    FIX88 — two shapes behind one entry point, chosen by ``type``:

    - GRANTS (``signup_bonus``, ``purchase``; see ``GRANT_TYPES``)
      delegate to :func:`grant_credits`: balance AND ceiling rise by
      ``amount``.
    - Everything else (``manual_topup``, ``adjustment``) FILLS towards
      the ceiling and raises :class:`CreditCeilingExceeded` when
      ``balance + amount > quota`` — the balance is untouched and no
      ledger row is written.

    ``cap_at_quota`` overrides the by-type default: ``True`` forces the
    capped fill (a purchase that must not raise the ceiling), ``False``
    forces grant semantics (the ceiling moves with the balance, so the
    ``balance <= quota`` invariant still holds). ``None`` = by type.
    """
    if amount <= 0:
        return None

    capped = (type not in GRANT_TYPES) if cap_at_quota is None else bool(cap_at_quota)
    if not capped:
        return grant_credits(
            db, user_id, amount, created_by, description, type=type
        )

    user = _lock_user(db, user_id)
    if user is None:
        return None

    balance = int(user.credit_balance)
    quota = int(user.credit_quota)
    room = max(0, quota - balance)
    if amount > room:
        raise CreditCeilingExceeded(
            f"Top-up of {amount:,} exceeds the credit ceiling: balance "
            f"{balance:,} of {quota:,}, room left {room:,}. Raise the "
            f"ceiling or top up at most {room:,}.",
            details={
                "balance": balance,
                "quota": quota,
                "room": room,
                "amount": amount,
            },
        )

    user.credit_balance = balance + amount
    db.flush()

    return _ledger_row(
        db,
        user,
        amount=amount,
        type=type,
        description=description,
        created_by=created_by,
    )


def fill_to_quota(
    db: Session,
    user_id: str,
    created_by: str,
    description: str | None = None,
) -> CreditTransactionORM | None:
    """Reset to full: ``balance := quota``.

    Writes ONE ledger row of type ``reset_to_full`` whose amount is
    exactly what was added (``quota − balance``). Returns ``None`` — and
    writes nothing — when the balance is already at the ceiling (no
    zero-amount rows) or the user does not exist. A balance ABOVE the
    ceiling (only possible for a row written outside this module) is
    left alone: this function only ever adds.
    """
    user = _lock_user(db, user_id)
    if user is None:
        return None

    delta = int(user.credit_quota) - int(user.credit_balance)
    if delta <= 0:
        return None

    user.credit_balance = int(user.credit_quota)
    db.flush()

    return _ledger_row(
        db,
        user,
        amount=delta,
        type="reset_to_full",
        description=description or "Reset to full",
        created_by=created_by,
    )


def set_quota(
    db: Session,
    user_id: str,
    new_quota: int,
    created_by: str,
    description: str | None = None,
) -> CreditTransactionORM | None:
    """Change the ceiling: ``quota := new_quota`` AND
    ``balance += (new_quota − old_quota)`` in one locked write.

    The owner's single rule — raising the ceiling by N adds N to the
    balance; lowering it removes N, floored at 0 (the ledger row
    records only what actually LEFT the balance, so a balance already
    at 0 records nothing).

    The ledger records BALANCE movements only: a row of type
    ``adjustment`` is written when and only when the balance moved. The
    ceiling change itself lives on the user row (``credit_quota`` +
    ``updated_at``); when the balance does not move (same quota, or a
    lowering while the balance is already 0) the quota is still
    written and ``None`` is returned.

    Refuses ``new_quota < 0`` with ``ValidationError(code=
    "invalid_quota")``. Returns ``None`` for an unknown user.
    """
    if int(new_quota) < 0:
        raise ValidationError(
            "Credit ceiling must be zero or positive.", code="invalid_quota"
        )

    user = _lock_user(db, user_id)
    if user is None:
        return None

    old_quota = int(user.credit_quota)
    balance = int(user.credit_balance)
    delta = int(new_quota) - old_quota

    if delta >= 0:
        moved = delta
    else:
        moved = -min(-delta, balance)

    user.credit_quota = int(new_quota)
    user.credit_balance = balance + moved
    db.flush()

    if moved == 0:
        return None

    return _ledger_row(
        db,
        user,
        amount=moved,
        type="adjustment",
        description=description
        or f"Credit ceiling {old_quota:,} → {int(new_quota):,}",
        created_by=created_by,
    )
