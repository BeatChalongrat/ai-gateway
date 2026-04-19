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
from sqlalchemy.orm import Session

from app.domain.dlp import EnforcementAction
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

ALLOWED_ACTIONS: tuple[str, ...] = ("allow", "mask", "block")

_ACTION_STR_TO_ENUM = {
    "allow": EnforcementAction.ALLOW,
    "mask": EnforcementAction.MASK,
    "block": EnforcementAction.BLOCK,
}
_ACTION_ENUM_TO_STR = {v: k for k, v in _ACTION_STR_TO_ENUM.items()}


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
        ),
        STRICT_PROFILE.profile_id: (
            STRICT_PROFILE.name,
            dict(STRICT_PROFILE.category_actions),
            dict(STRICT_PROFILE.category_actions_output)
            if STRICT_PROFILE.category_actions_output is not None
            else None,
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
    for profile_id, (name, actions, outbound_actions) in builtins.items():
        merged = _merge(actions, db_overrides.get(profile_id, {}))
        views.append(
            ProfileView(
                profile_id=profile_id,
                name=db_names.get(profile_id, name),
                category_actions=merged,
                category_actions_output=_actions_to_str(outbound_actions),
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
            )
        )
    return views


def get_profile_view(session: Session, profile_id: str) -> ProfileView | None:
    for view in list_profiles(session):
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
    row.category_actions_json = json.dumps(overrides)
    session.flush()

    view = get_profile_view(session, profile_id)
    assert view is not None
    return view
