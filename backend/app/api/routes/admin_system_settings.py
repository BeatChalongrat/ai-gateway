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

from app.api.deps import get_db_session, require_admin_session
from app.core.exceptions import NotFoundError, ValidationError
from app.models.system_setting import SystemSettingORM


router = APIRouter(dependencies=[Depends(require_admin_session)])


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


def _validate_provider_base_url_setting(value: str) -> str:
    """Provider endpoint rows: empty clears the override, otherwise the
    URL passes the same lenient SSRF gate the add-key route uses."""
    from app.services.providers.base_urls import validate_provider_base_url

    v = (value or "").strip()
    if not v:
        return ""
    return validate_provider_base_url(v)


def _validate_auto_model(value: str) -> str:
    """A single model id that Auto may elect.

    Must be a model the registry knows AND one that can call tools —
    Auto turns carry the memory/search tool set, and electing a
    tools-blind model drops those calls silently.
    """
    from app.services.providers.model_registry import get_candidate

    v = (value or "").strip()
    if not v:
        raise ValidationError("Pick a model.", code="invalid_value")
    cand = get_candidate(v)
    if cand is None:
        raise ValidationError(
            f"Unknown model '{v}'.", code="invalid_value"
        )
    if not getattr(cand, "supports_tools", False):
        raise ValidationError(
            f"'{v}' does not support tool calling.", code="invalid_value"
        )
    return v


def _validate_auto_fallbacks(value: str) -> str:
    """Comma-separated fallback chain. Empty is allowed (no chain)."""
    from app.services.routing_settings import parse_model_list

    models = parse_model_list(value)
    for model in models:
        _validate_auto_model(model)
    return ",".join(models)


def _validate_routing_tier(value: str) -> str:
    """FIX35 Part C — the Auto router's deployment-wide cost ceiling."""
    from app.services.auto_router.tiers import TIERS

    v = (value or "").strip().lower()
    if v not in TIERS:
        raise ValueError(f"must be one of: {', '.join(TIERS)}")
    from app.services.auto_router import settings as _auto_settings

    _auto_settings.clear_cache()
    return v


def _validate_media_order(value: str) -> str:
    """FIX67 — a comma-separated ``provider:model`` order. Format only; the
    cross-field checks (mock kill-switch, known model, no cost upgrade, ≤2
    tiers) happen in ``_check_media_order`` with a session. Empty is allowed
    (no fallback / no destination)."""
    from app.services.media_gen.destinations import parse_order

    raw = (value or "").strip()
    if not raw:
        return ""
    dests = parse_order(raw)
    if not dests:
        raise ValidationError(
            "Each entry must be 'provider:model' (comma-separated).",
            code="invalid_value",
        )
    return ", ".join(f"{d.provider}:{d.model}" for d in dests)


_VALIDATORS: dict[str, Callable[[str], str]] = {
    "auto_routing_tier_default": _validate_routing_tier,
    "free_trial_tokens": _validate_positive_int,
    "trial_model_tier": _validate_model_tier,
    "portal_signup_enabled": _validate_bool,
    "credit_low_threshold": _validate_positive_int,
    "provider_base_url:sranai": _validate_provider_base_url_setting,
    "auto_default_model": _validate_auto_model,
    "auto_default_fallbacks": _validate_auto_fallbacks,
    "media_order:image": _validate_media_order,
    "media_order:video": _validate_media_order,
    "media_allow_mock": _validate_bool,
}


# Cost tiers, cheapest first — same ordering smart_router uses.
_TIER_RANK = {"cheap": 0, "budget": 0, "standard": 1, "premium": 2}


def _tier_rank(tier: str | None) -> int:
    return _TIER_RANK.get((tier or "").strip().lower(), 1)


def _check_tier_ceiling(key: str, value: str, session: Session) -> None:
    """Keep the Auto default at or under the max-tier ceiling.

    ``trial_model_tier`` is the "Budget maximum tier" card; the Auto
    default is the quality floor. A floor above the ceiling is
    incoherent, so whichever side moves is rejected with a message
    naming the other one. Only these two keys interact.
    """
    if key not in ("auto_default_model", "trial_model_tier"):
        return

    from app.services.providers.model_registry import get_candidate

    def _current(k: str) -> str | None:
        row = session.get(SystemSettingORM, k)
        return row.value if row is not None else None

    if key == "auto_default_model":
        model, ceiling = value, _current("trial_model_tier")
    else:
        model, ceiling = _current("auto_default_model"), value
    if not model or not ceiling:
        return

    cand = get_candidate(model)
    if cand is None:
        return
    if _tier_rank(cand.cost_tier) > _tier_rank(ceiling):
        raise ValidationError(
            f"'{model}' is a {cand.cost_tier}-tier model but the maximum "
            f"tier is '{ceiling}'. Raise the maximum tier first.",
            code="invalid_value",
        )


def _check_media_order(key: str, value: str, session: Session) -> None:
    """FIX67 cross-field rules for a media destination order (with a session).

    * ≤2 tiers, each a different provider;
    * a ``mock`` destination is refused while ``media_allow_mock`` is off — the
      config-time half of the mock leak guard (a test enforces the runtime half);
    * every ``(provider, model)`` must exist as a catalogue row of the right
      capability, with a registered backend;
    * the second tier must not be a costlier model than the first (never upgrade).
    """
    if key not in ("media_order:image", "media_order:video"):
        return
    from app.models.model_config import ModelConfigORM
    from app.services.media_gen.destinations import (
        MAX_TIERS,
        is_mock_provider,
        parse_order,
    )

    media_type = "image" if key.endswith(":image") else "video"
    cap_col = "supports_image_gen" if media_type == "image" else "supports_video_gen"
    dests = parse_order(value)
    if not dests:
        return
    if len(dests) > MAX_TIERS:
        raise ValidationError(
            f"At most {MAX_TIERS} destinations (a primary and one fallback).",
            code="invalid_value",
        )
    if len({d.provider for d in dests}) != len(dests):
        raise ValidationError(
            "Each tier must be a different provider.", code="invalid_value",
        )

    mock_on = _current_bool(session, "media_allow_mock")
    costs: list[float] = []
    for d in dests:
        if is_mock_provider(d.provider) and not mock_on:
            raise ValidationError(
                "A 'mock' destination is not allowed unless media_allow_mock is "
                "on. Turn on media_allow_mock first (test deployments only).",
                code="invalid_value",
            )
        row = (
            session.query(ModelConfigORM)
            .filter(
                ModelConfigORM.provider == d.provider,
                ModelConfigORM.model == d.model,
            )
            .first()
        )
        if row is None or not getattr(row, cap_col, False):
            raise ValidationError(
                f"'{d.provider}:{d.model}' is not a known {media_type} model.",
                code="invalid_value",
            )
        costs.append(float(row.cost_per_input_token or 0.0))
    # never upgrade: the fallback must not cost more than the primary.
    if len(costs) == 2 and costs[1] > costs[0]:
        raise ValidationError(
            "The fallback must not be a costlier model than the primary.",
            code="invalid_value",
        )


def _current_bool(session: Session, key: str) -> bool:
    row = session.get(SystemSettingORM, key)
    return bool(row) and (row.value or "").strip().lower() in ("true", "1", "yes", "on")


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

    validated = validator(body.value)
    _check_tier_ceiling(key, validated, session)
    _check_media_order(key, validated, session)
    if key.startswith("provider_base_url:"):
        # FIX85 — this row is the provider-level endpoint, and an edit
        # here is the same move the providers page makes: it must retire
        # the verdicts of the keys that inherit it. Before this it wrote
        # the value directly, so a key tested against the old box kept
        # saying "valid" — the exact hole ae949b2 closed on the other
        # route.
        from app.services.providers.base_urls import set_stored_base_url

        set_stored_base_url(session, key[len("provider_base_url:"):], validated)
        session.flush()
        session.refresh(row)
    else:
        row.value = validated
        session.flush()
    if key in ("auto_default_model", "auto_default_fallbacks"):
        # Take effect on the next turn instead of after the read TTL —
        # the whole point is "change it without a deploy".
        from app.services.routing_settings import clear_cache

        clear_cache()
    if key.startswith("provider_base_url:"):
        # Propagate an endpoint edit without waiting out the 30s TTL.
        # (Pool eviction of the old URL is skipped on this sync route —
        # the stale client only idles; the add-key route does full evict.)
        from app.services.providers.base_urls import clear_base_url_cache

        clear_base_url_cache()
    return {
        "key": row.key,
        "value": row.value,
        "description": row.description,
        "updated_at": row.updated_at,
    }
