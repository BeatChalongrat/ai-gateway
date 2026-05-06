"""Policy domain model for the Secure AI Gateway.

Defines the PolicyProfile — the configuration that controls how DLP
findings are translated into enforcement actions.

Design constraints:
- Framework-agnostic: only stdlib imports + domain enums.
- No enforcement logic here — this is data only.
- category_actions uses a dict so new categories don't require schema changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.dlp import EnforcementAction, SensitivityLevel


@dataclass(frozen=True)
class PolicyProfile:
    """Configuration that drives DLP enforcement for a project/tenant.

    Attributes
    ----------
    profile_id : str
        Unique identifier (e.g. "default", "strict-finance").
    name : str
        Human-readable display name.
    category_actions : dict[str, EnforcementAction]
        Maps finding category → action for INBOUND scans (request
        messages headed to the provider).
        e.g. {"pii": MASK, "secret": BLOCK, "financial": BLOCK}
        Findings whose category is absent default to ALLOW.
    category_actions_output : dict[str, EnforcementAction] | None
        Optional override for OUTBOUND scans (provider response
        headed back to the user). When ``None`` (the default), the
        evaluator falls back to ``category_actions`` so existing
        profiles keep their pre-Fix-3 behavior. When set, this dict
        replaces ``category_actions`` for output-direction findings —
        typical use is to mask only credentials/secrets the AI
        shouldn't leak while letting the AI's answers about other
        sensitive-looking data (test cards, example IPs, fictional
        emails) flow through to the user untouched.
    severity_threshold_block : SensitivityLevel | None
        Any finding at or above this severity is BLOCKED regardless
        of its category action.  None disables this check.
    scan_responses : bool
        Whether provider responses should be scanned.
    scan_system_messages : bool
        Whether system-role messages should be included in DLP scanning.
        Default False — operator-authored system prompts (e.g. "Reply
        in Thai.") routinely contain words that NER classifies as
        PERSON / NRP entities, polluting span_map with bindings the
        user never authored. Tenants who put real PII in system
        prompts can opt-in by setting this to True on their profile.
    """

    profile_id: str
    name: str
    category_actions: dict[str, EnforcementAction] = field(default_factory=dict)
    category_actions_output: dict[str, EnforcementAction] | None = None
    severity_threshold_block: SensitivityLevel | None = None
    scan_responses: bool = True
    moderation_enabled: bool = False
    scan_system_messages: bool = False
