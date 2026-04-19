"""Built-in policy profiles and profile lookup.

These in-memory profiles serve as the baseline until DB-backed profiles
are implemented.  ``get_profile()`` is the abstraction point — callers
import it, not the profile instances directly, so the backing store
can change without touching consumers.
"""

from __future__ import annotations

from app.domain.dlp import EnforcementAction, SensitivityLevel
from app.domain.policy import PolicyProfile

# ---------------------------------------------------------------------------
# Built-in profiles
# ---------------------------------------------------------------------------

DEFAULT_PROFILE = PolicyProfile(
    profile_id="default",
    name="Default",
    category_actions={
        "pii": EnforcementAction.MASK,
        "financial": EnforcementAction.MASK,
        "network": EnforcementAction.MASK,
        "identity": EnforcementAction.MASK,
        "credential": EnforcementAction.MASK,
        # "secret" is a legacy category used by bearer_token / password_field /
        # private_key detectors. It overlaps with "credential" semantically, so
        # we MASK (not BLOCK) here in the default profile — credentials are
        # still never sent to the provider, but the request continues with a
        # placeholder so users get a response rather than a hard failure.
        "secret": EnforcementAction.MASK,
    },
    # Outbound override (Fix 3). Only mask categories that the AI should
    # never leak back to the user regardless of context — credentials and
    # secret tokens. PII / financial / network / identity findings on the
    # outbound side fall through to ALLOW (categories absent from this
    # dict default to ALLOW per evaluator logic), so AI answers about
    # public test card numbers, RFC-1918 IPs, fictional emails, and
    # similar reference data reach the user untouched. Inbound behavior
    # is unchanged.
    category_actions_output={
        "credential": EnforcementAction.MASK,
        "secret": EnforcementAction.MASK,
    },
    severity_threshold_block=None,  # rely on category rules only
    scan_responses=True,
)

STRICT_PROFILE = PolicyProfile(
    profile_id="strict",
    name="Strict",
    category_actions={
        "pii": EnforcementAction.MASK,
        "financial": EnforcementAction.BLOCK,
        "network": EnforcementAction.BLOCK,
        "identity": EnforcementAction.BLOCK,
        "credential": EnforcementAction.BLOCK,
        "secret": EnforcementAction.BLOCK,
    },
    # category_actions_output left at None on purpose — strict profile
    # falls back to the inbound dict for the outbound path, preserving
    # its pre-Fix-3 behavior (block everything sensitive both ways).
    category_actions_output=None,
    severity_threshold_block=SensitivityLevel.CRITICAL,
    scan_responses=True,
    moderation_enabled=True,
)

# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

_PROFILES: dict[str, PolicyProfile] = {
    DEFAULT_PROFILE.profile_id: DEFAULT_PROFILE,
    STRICT_PROFILE.profile_id: STRICT_PROFILE,
}


def get_profile(profile_id: str) -> PolicyProfile:
    """Look up a profile by ID.  Falls back to DEFAULT_PROFILE if not found."""
    return _PROFILES.get(profile_id, DEFAULT_PROFILE)


def profile_exists(profile_id: str) -> bool:
    """Return True iff *profile_id* is a registered built-in profile.

    Unlike ``get_profile`` this does NOT fall back to DEFAULT_PROFILE
    — callers that need to reject unknown profile IDs (e.g. the
    per-request ``policy_profile_id`` override validator in
    ``schemas/public.py``) should use this.
    """
    return profile_id in _PROFILES


# ---------------------------------------------------------------------------
# Override permission
# ---------------------------------------------------------------------------

# Severity ranking for the per-request override gate. A caller may
# override to a profile at least as restrictive as the project default;
# anything less restrictive is rejected (403) in project_service.
#
# Profiles not in this map are treated as "default" severity, which
# means custom DB profiles can only be overridden to "strict".
_PROFILE_SEVERITY: dict[str, int] = {
    "strict": 2,
    "default": 1,
}


def is_override_permitted(
    requested: str, project_profile: str | None
) -> bool:
    """Decide whether a per-request profile override is allowed.

    Rule: ``override_severity >= project_profile_severity``.

    - strict (2) can override default (1)  → allowed
    - strict (2) can override strict  (2)  → allowed (same profile)
    - default (1) can override default (1) → allowed (no-op override)
    - default (1) can override strict  (2) → REJECTED — escalation attempt

    Unknown profile IDs (e.g. custom DB profiles once they exist) are
    treated as severity 1 ("default"). That means a custom profile
    can only be overridden to "strict" in v1 — not to another custom.
    """
    req_sev = _PROFILE_SEVERITY.get(requested, 1)
    proj_sev = _PROFILE_SEVERITY.get(project_profile or "default", 1)
    return req_sev >= proj_sev
