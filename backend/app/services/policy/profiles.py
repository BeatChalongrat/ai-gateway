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
    # Outbound action map (DLP-coverage tightening, May 2026).
    #
    # Previous behaviour masked only credentials/secrets on the
    # outbound side and let PII fall through to ALLOW. That left a
    # gap where an LLM hallucinating an email / phone / Thai ID
    # would deliver the fake value verbatim to the end user, and a
    # gap where an LLM echoing back the user's own PII (after the
    # masked-token round-trip) could surface real values if the
    # vault failed to restore.
    #
    # New defaults RESTORE the same categories outbound as inbound
    # masked — including ``network``. The original default left
    # ``network`` at ALLOW on the theory that a user asking about an
    # RFC-1918 range still wanted the AI to mention it freely, but in
    # the round-trip case ("confirm these IPs back to me") the user
    # got ``[IP_ADDRESS_0]`` placeholders instead of their own IPs.
    # RESTORE here means: the IP tokens we minted on the inbound side
    # get swapped back to the user-supplied originals when the AI
    # echoes them; fresh IPs the AI volunteers are still masked
    # because the orchestrator only restores tokens that exist in the
    # request-scoped span_map.
    #
    # Tenants who explicitly want raw outbound PII (e.g. assistants
    # that need to repeat a user-typed address) should opt into a
    # custom profile rather than relax the default for everyone.
    category_actions_output={
        # Credentials and secrets MUST NOT be RESTORED on outbound.
        # A round-trip echo (user pastes an API key → LLM repeats the
        # masked ``<r0>`` token → restore swaps back to the literal
        # key) leaks the secret to the user's reply. MASK keeps the
        # placeholder in place; orphan-redact strips it. Defense-in-
        # depth in orchestrator._compute_restore_allowed_tokens also
        # hard-blocks these two categories regardless of profile —
        # this entry is the principled policy, that helper is the
        # belt-and-braces.
        "credential": EnforcementAction.MASK,
        "secret":     EnforcementAction.MASK,
        # Identity-class PII stays on RESTORE so the user gets their
        # own values echoed back legibly ("confirm these emails back
        # to me"). The orchestrator only restores tokens minted from
        # THIS request's inbound span_map, so cross-user leaks are
        # impossible — see _compute_restore_allowed_tokens.
        "pii":        EnforcementAction.RESTORE,
        "identity":   EnforcementAction.RESTORE,
        "financial":  EnforcementAction.RESTORE,
        "network":    EnforcementAction.RESTORE,
        # thai_name isn't a runtime category today (all Thai-name
        # detectors emit ``pii``) — listed explicitly so a future
        # detector that does choose a dedicated category gets the
        # right treatment without another profile edit.
        "thai_name":  EnforcementAction.RESTORE,
    },
    # Fallback block threshold for a CRITICAL finding whose category has NO
    # explicit entry in category_actions above. NOTE: credential/secret DO
    # have explicit MASK entries, and evaluator._decide lets an explicit
    # category_action win over this threshold — so in THIS default profile
    # api keys / bearer tokens / private keys / DB strings are MASKED, not
    # blocked (the secret is replaced with a placeholder and never egresses
    # raw, but the request still proceeds so the user gets a response). This
    # threshold therefore only escalates to BLOCK for some future CRITICAL
    # category left unmapped above. Tenants who want a HARD REFUSAL on
    # credentials use STRICT_PROFILE (credential/secret -> BLOCK).
    severity_threshold_block=SensitivityLevel.CRITICAL,
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

# Media-generation profile (Sprint 1 — multi-modal generation foundation).
#
# Big media-gen providers (OpenAI, Google, Replicate, ElevenLabs, Suno) do
# NOT mask end-user PII in generation prompts — they pass it through and
# enforce CONTENT policy instead (audit v2, 2026-06-22). Masking a user's
# own name/address in their own image/video prompt only degrades output
# quality for no compliance gain, so this profile lets identity-class PII
# PASS while still hard-BLOCKING anything that must never egress raw to a
# third-party provider (credentials, secrets, financial instruments).
#
# Mapping to runtime categories:
#   - pii / identity / network  -> ALLOW (pass-through). Listed EXPLICITLY
#     (not left to the absent-category default) so the explicit action wins
#     over ``severity_threshold_block`` — a CRITICAL-severity identity
#     finding (e.g. thai_national_id) still passes, by design.
#   - credential / secret / financial -> BLOCK.
#   - The PDPA-sensitive detectors (medical_record, health_condition, …)
#     emit category ``pii`` at runtime, so ``pii: ALLOW`` already covers
#     them — there is no separate ``pdpa_sensitive`` category to map.
#
# NOT wired to anything in Sprint 1: ``public_images.py`` still uses the
# DEFAULT profile. Sprint 2 retrofits the image path + new media routes to
# select this profile. Per-tenant ``DLP_MEDIA_GEN_MASK_IDENTITY`` (the
# tenants column added in migration 056) will let enterprise tenants opt
# back into masking later.
MEDIA_GEN_PROFILE = PolicyProfile(
    profile_id="media_gen",
    name="Media Generation",
    category_actions={
        "pii": EnforcementAction.ALLOW,
        "identity": EnforcementAction.ALLOW,
        "network": EnforcementAction.ALLOW,
        "financial": EnforcementAction.BLOCK,
        "credential": EnforcementAction.BLOCK,
        "secret": EnforcementAction.BLOCK,
    },
    # No separate outbound map — media output is binary (image/video/audio),
    # not scannable text, and ``scan_responses`` is off below.
    category_actions_output=None,
    severity_threshold_block=SensitivityLevel.CRITICAL,
    # Generated media is a binary artifact; there is no text response to
    # scan back to the user.
    scan_responses=False,
)

# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

_PROFILES: dict[str, PolicyProfile] = {
    DEFAULT_PROFILE.profile_id: DEFAULT_PROFILE,
    STRICT_PROFILE.profile_id: STRICT_PROFILE,
    MEDIA_GEN_PROFILE.profile_id: MEDIA_GEN_PROFILE,
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
