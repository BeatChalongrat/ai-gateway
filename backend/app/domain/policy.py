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
        Maps finding category → action.
        e.g. {"pii": MASK, "secret": BLOCK, "financial": BLOCK}
        Findings whose category is absent default to ALLOW.
    severity_threshold_block : SensitivityLevel | None
        Any finding at or above this severity is BLOCKED regardless
        of its category action.  None disables this check.
    scan_responses : bool
        Whether provider responses should be scanned.
    """

    profile_id: str
    name: str
    category_actions: dict[str, EnforcementAction] = field(default_factory=dict)
    severity_threshold_block: SensitivityLevel | None = None
    scan_responses: bool = True
    moderation_enabled: bool = False
