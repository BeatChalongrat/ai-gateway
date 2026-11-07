"""Service that merges built-in policy profiles with DB-backed overrides.

Source of truth layering:
    1. Built-ins (``profiles.py``) — hardcoded defaults compiled into the app.
    2. ``policy_profiles`` table — per-category action overrides edited by
       super-admins via the admin UI.

Runtime behavior: DB overrides take precedence when present. The built-in
dict is copied first, then DB-provided category→action pairs overwrite.
This way an unknown profile still falls back to DEFAULT_PROFILE and a DB
row that only sets e.g. ``network`` leaves the other categories untouched.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.domain.dlp import EnforcementAction, SensitivityLevel
from app.domain.policy import PolicyProfile
from app.models.policy_profile import PolicyProfileORM
from app.services.policy.profiles import DEFAULT_PROFILE, STRICT_PROFILE

# Canonical category list shown in the admin UI policy editor. Keep in sync
# with the detector ``category`` attributes in services/dlp/detectors/*.
KNOWN_CATEGORIES: tuple[str, ...] = (
    "network",
    "identity",
    "financial",
    "pii",
    "credential",
    "secret",
    "ner",
)

ALLOWED_ACTIONS: tuple[str, ...] = (
    "allow",
    "restore",
    "unmask",
    "allow_restore",
    "mask",
    "block",
)

_ACTION_STR_TO_ENUM = {
    "allow": EnforcementAction.ALLOW,
    "restore": EnforcementAction.RESTORE,
    "unmask": EnforcementAction.RESTORE,
    "allow_restore": EnforcementAction.RESTORE,
    "mask": EnforcementAction.MASK,
    "block": EnforcementAction.BLOCK,
}
_ACTION_ENUM_TO_STR = {
    EnforcementAction.ALLOW: "allow",
    EnforcementAction.RESTORE: "restore",
    EnforcementAction.MASK: "mask",
    EnforcementAction.BLOCK: "block",
}


@dataclass
class ProfileView:
    """Read model returned to the admin UI."""

    profile_id: str
    name: str
    category_actions: dict[str, str]  # inbound: category → "allow"|"mask"|"block"
    # Outbound overrides. ``None`` means the profile inherits the inbound
    # actions for responses (Fix 3 contract — evaluator falls back to
    # ``category_actions`` when ``category_actions_output`` is None). The
    # admin UI renders this as "(inherited)" next to the outbound cell.
    category_actions_output: dict[str, str] | None = None
    severity_threshold_block: str | None = None


def _actions_to_str(
    actions: dict[str, EnforcementAction] | None,
) -> dict[str, str] | None:
    if actions is None:
        return None
    return {cat: _ACTION_ENUM_TO_STR.get(act, "allow") for cat, act in actions.items()}


def _builtin_map() -> dict[
    str,
    tuple[
        str,
        dict[str, EnforcementAction],
        dict[str, EnforcementAction] | None,
        SensitivityLevel | None,
        bool,
        bool,
    ],
]:
    """Snapshot of built-in profiles keyed by profile_id.

    Tuple shape: (name, inbound_actions, outbound_actions_or_None).
    """
    return {
        DEFAULT_PROFILE.profile_id: (
            DEFAULT_PROFILE.name,
            dict(DEFAULT_PROFILE.category_actions),
            dict(DEFAULT_PROFILE.category_actions_output)
            if DEFAULT_PROFILE.category_actions_output is not None
            else None,
            DEFAULT_PROFILE.severity_threshold_block,
            DEFAULT_PROFILE.scan_responses,
            DEFAULT_PROFILE.moderation_enabled,
        ),
        STRICT_PROFILE.profile_id: (
            STRICT_PROFILE.name,
            dict(STRICT_PROFILE.category_actions),
            dict(STRICT_PROFILE.category_actions_output)
            if STRICT_PROFILE.category_actions_output is not None
            else None,
            STRICT_PROFILE.severity_threshold_block,
            STRICT_PROFILE.scan_responses,
            STRICT_PROFILE.moderation_enabled,
        ),
    }


def _merge(
    builtin: dict[str, EnforcementAction],
    overrides: dict[str, str],
) -> dict[str, str]:
    """Merge built-in category_actions with DB overrides.

    Built-in values are converted to lowercase strings; overrides replace
    any category they set. Unknown categories in the override are ignored
    at read time — validation at write time prevents bad data.
    """
    out: dict[str, str] = {
        cat: _ACTION_ENUM_TO_STR.get(act, "allow")
        for cat, act in builtin.items()
    }
    # Ensure every canonical category has an entry (default "allow"
    # when the built-in profile doesn't mention it).
    for cat in KNOWN_CATEGORIES:
        out.setdefault(cat, "allow")
    for cat, action in overrides.items():
        if cat in KNOWN_CATEGORIES and action in ALLOWED_ACTIONS:
            out[cat] = action
    return out


def list_profiles(session: Session) -> list[ProfileView]:
    """Return all profiles (built-ins plus any DB-custom ones).

    The built-in list is always included; DB rows for a built-in profile
    merge into that built-in. Custom DB-only profiles are appended after
    the built-ins in insertion order.
    """
    builtins = _builtin_map()
    rows = session.execute(
        select(PolicyProfileORM).order_by(PolicyProfileORM.created_at)
    ).scalars().all()

    db_overrides: dict[str, dict[str, str]] = {}
    db_names: dict[str, str] = {}
    for row in rows:
        try:
            data = json.loads(row.category_actions_json or "{}")
        except json.JSONDecodeError:
            data = {}
        db_overrides[row.profile_id] = {
            str(k): str(v) for k, v in data.items() if isinstance(v, str)
        }
        db_names[row.profile_id] = row.name

    views: list[ProfileView] = []
    seen: set[str] = set()
    for profile_id, (name, actions, outbound_actions, threshold, _scan_responses, _moderation) in builtins.items():
        merged = _merge(actions, db_overrides.get(profile_id, {}))
        views.append(
            ProfileView(
                profile_id=profile_id,
                name=db_names.get(profile_id, name),
                category_actions=merged,
                category_actions_output=_actions_to_str(outbound_actions),
                severity_threshold_block=(
                    threshold.name.lower() if threshold is not None else None
                ),
            )
        )
        seen.add(profile_id)

    # Custom DB-only profiles (not shipped with the app). They have no
    # outbound override channel yet, so outbound inherits inbound (None).
    for profile_id, overrides in db_overrides.items():
        if profile_id in seen:
            continue
        views.append(
            ProfileView(
                profile_id=profile_id,
                name=db_names.get(profile_id, profile_id),
                category_actions=_merge({}, overrides),
                category_actions_output=None,
                severity_threshold_block=None,
            )
        )
    return views


async def list_profiles_async(session: AsyncSession) -> list[ProfileView]:
    """Async sibling of :func:`list_profiles`.

    Identical logic; only the single ``session.execute`` DB call is awaited.
    ``PolicyProfileORM`` has no relationships (all scalar columns), so no
    eager loading is required.
    """
    builtins = _builtin_map()
    result = await session.execute(
        select(PolicyProfileORM).order_by(PolicyProfileORM.created_at)
    )
    rows = result.scalars().all()

    db_overrides: dict[str, dict[str, str]] = {}
    db_names: dict[str, str] = {}
    for row in rows:
        try:
            data = json.loads(row.category_actions_json or "{}")
        except json.JSONDecodeError:
            data = {}
        db_overrides[row.profile_id] = {
            str(k): str(v) for k, v in data.items() if isinstance(v, str)
        }
        db_names[row.profile_id] = row.name

    views: list[ProfileView] = []
    seen: set[str] = set()
    for profile_id, (name, actions, outbound_actions, threshold, _scan_responses, _moderation) in builtins.items():
        merged = _merge(actions, db_overrides.get(profile_id, {}))
        views.append(
            ProfileView(
                profile_id=profile_id,
                name=db_names.get(profile_id, name),
                category_actions=merged,
                category_actions_output=_actions_to_str(outbound_actions),
                severity_threshold_block=(
                    threshold.name.lower() if threshold is not None else None
                ),
            )
        )
        seen.add(profile_id)

    # Custom DB-only profiles (not shipped with the app). They have no
    # outbound override channel yet, so outbound inherits inbound (None).
    for profile_id, overrides in db_overrides.items():
        if profile_id in seen:
            continue
        views.append(
            ProfileView(
                profile_id=profile_id,
                name=db_names.get(profile_id, profile_id),
                category_actions=_merge({}, overrides),
                category_actions_output=None,
                severity_threshold_block=None,
            )
        )
    return views


def _actions_to_enum(actions: dict[str, str]) -> dict[str, EnforcementAction]:
    return {
        cat: _ACTION_STR_TO_ENUM[action]
        for cat, action in actions.items()
        if action in _ACTION_STR_TO_ENUM
    }


def get_runtime_profile(session: Session, profile_id: str) -> PolicyProfile:
    """Return the effective runtime profile using the same DB overrides as UI."""
    builtins = _builtin_map()
    base = builtins.get(profile_id)
    view = get_profile_view(session, profile_id)
    if view is None:
        from app.services.policy.profiles import get_profile

        return get_profile(profile_id)

    threshold = base[3] if base is not None else None
    scan_responses = base[4] if base is not None else True
    moderation_enabled = base[5] if base is not None else False
    return PolicyProfile(
        profile_id=view.profile_id,
        name=view.name,
        category_actions=_actions_to_enum(view.category_actions),
        category_actions_output=(
            _actions_to_enum(view.category_actions_output)
            if view.category_actions_output is not None
            else None
        ),
        severity_threshold_block=threshold,
        scan_responses=scan_responses,
        moderation_enabled=moderation_enabled,
    )


def get_profile_view(session: Session, profile_id: str) -> ProfileView | None:
    for view in list_profiles(session):
        if view.profile_id == profile_id:
            return view
    return None


async def get_runtime_profile_async(
    session: AsyncSession, profile_id: str
) -> PolicyProfile:
    """Async sibling of :func:`get_runtime_profile`.

    Identical merge logic; the only difference is that the DB-backed
    profile-view lookup is awaited. The built-in fallback path
    (``get_profile``) is pure in-memory and reused as-is.
    """
    builtins = _builtin_map()
    base = builtins.get(profile_id)
    view = await get_profile_view_async(session, profile_id)
    if view is None:
        from app.services.policy.profiles import get_profile

        return get_profile(profile_id)

    threshold = base[3] if base is not None else None
    scan_responses = base[4] if base is not None else True
    moderation_enabled = base[5] if base is not None else False
    return PolicyProfile(
        profile_id=view.profile_id,
        name=view.name,
        category_actions=_actions_to_enum(view.category_actions),
        category_actions_output=(
            _actions_to_enum(view.category_actions_output)
            if view.category_actions_output is not None
            else None
        ),
        severity_threshold_block=threshold,
        scan_responses=scan_responses,
        moderation_enabled=moderation_enabled,
    )


async def get_profile_view_async(
    session: AsyncSession, profile_id: str
) -> ProfileView | None:
    """Async sibling of :func:`get_profile_view`."""
    for view in await list_profiles_async(session):
        if view.profile_id == profile_id:
            return view
    return None


def set_category_action(
    session: Session,
    profile_id: str,
    category: str,
    action: str,
) -> ProfileView:
    """Upsert a single category action override for *profile_id*.

    Raises ValueError on unknown category or action. Callers (API layer)
    translate the ValueError into an HTTP 422.
    """
    if category not in KNOWN_CATEGORIES:
        raise ValueError(f"Unknown category '{category}'")
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"Unknown action '{action}'")

    builtins = _builtin_map()
    # Reject writes for unknown profile IDs that aren't built-in and have
    # no existing DB row. Creating custom profiles is a separate feature.
    if profile_id not in builtins:
        existing = session.get(PolicyProfileORM, profile_id)
        if existing is None:
            raise ValueError(f"Unknown profile '{profile_id}'")

    row = session.get(PolicyProfileORM, profile_id)
    if row is None:
        builtin_name = builtins[profile_id][0] if profile_id in builtins else profile_id
        row = PolicyProfileORM(
            profile_id=profile_id,
            name=builtin_name,
            category_actions_json="{}",
        )
        session.add(row)
        session.flush()

    try:
        overrides = json.loads(row.category_actions_json or "{}")
        if not isinstance(overrides, dict):
            overrides = {}
    except json.JSONDecodeError:
        overrides = {}
    overrides[category] = action
    row.category_actions_json = json.dumps(overrides, ensure_ascii=False)
    session.flush()

    view = get_profile_view(session, profile_id)
    assert view is not None
    return view
