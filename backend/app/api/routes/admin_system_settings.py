"""Admin system_settings CRUD.

Only the known keys accept a PATCH — arbitrary new keys are rejected,
and each known key has a per-key validator so an admin can't set
``free_trial_tokens = "banana"`` (and then watch signup crash).
"""

from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db_session, require_admin
from app.core.exceptions import NotFoundError, ValidationError
from app.models.system_setting import SystemSettingORM


router = APIRouter(dependencies=[Depends(require_admin)])


class SettingUpdate(BaseModel):
    value: str


def _validate_positive_int(value: str) -> str:
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ValidationError("Value must be an integer.", code="invalid_value")
    if n <= 0:
        raise ValidationError("Value must be positive.", code="invalid_value")
    return str(n)


def _validate_model_tier(value: str) -> str:
    v = (value or "").strip().lower()
    if v not in ("budget", "standard", "premium"):
        raise ValidationError(
            "Tier must be one of: budget, standard, premium.",
            code="invalid_value",
        )
    return v


def _validate_bool(value: str) -> str:
    v = (value or "").strip().lower()
    if v in ("true", "1", "yes", "on"):
        return "true"
    if v in ("false", "0", "no", "off"):
        return "false"
    raise ValidationError("Value must be true or false.", code="invalid_value")


_VALIDATORS: dict[str, Callable[[str], str]] = {
    "free_trial_tokens": _validate_positive_int,
    "trial_model_tier": _validate_model_tier,
    "portal_signup_enabled": _validate_bool,
    "credit_low_threshold": _validate_positive_int,
}


@router.get("")
def list_settings(session: Session = Depends(get_db_session)):
    rows = (
        session.execute(
            select(SystemSettingORM).order_by(SystemSettingORM.key.asc())
        )
        .scalars()
        .all()
    )
    return [
        {
            "key": r.key,
            "value": r.value,
            "description": r.description,
            "updated_at": r.updated_at,
        }
        for r in rows
    ]


@router.patch("/{key}")
def update_setting(
    key: str,
    body: SettingUpdate,
    session: Session = Depends(get_db_session),
):
    row = session.get(SystemSettingORM, key)
    if row is None:
        raise NotFoundError("Unknown setting.", code="setting_not_found")

    validator = _VALIDATORS.get(key)
    if validator is None:
        # Only whitelisted keys are mutable. Arbitrary keys require a
        # new migration + a validator rather than an ad-hoc DB write.
        raise ValidationError(
            "This setting is not editable via the API.",
            code="setting_not_editable",
        )

    row.value = validator(body.value)
    session.flush()
    return {
        "key": row.key,
        "value": row.value,
        "description": row.description,
        "updated_at": row.updated_at,
    }
