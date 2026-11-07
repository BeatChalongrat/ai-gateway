"""Policy evaluator: maps DLP findings to enforcement actions.

Consumes a DetectionResult (with classified matches) and a PolicyProfile,
produces an EnforcementDecision with per-match MatchActions.

Pipeline position:
    Detectors → Classifier → **Evaluator** → Transformer

Design constraints:
- No detection logic (no regex, no scanning).
- No transformation logic (no masking, no redaction).
- Stateless: all context comes from the inputs.
"""

from __future__ import annotations

from app.domain.dlp import (
    DetectionMatch,
    DetectionResult,
    EnforcementAction,
    EnforcementDecision,
    MatchAction,
)
from app.domain.policy import PolicyProfile


class PolicyEvaluator:
    """Evaluates DLP findings against a policy profile.

    For each match, the evaluator decides an action by checking (in order):
    1. category_actions — look up the match's category → mapped action
    2. severity_threshold_block — fallback block for categories without an
       explicit action
    3. fallback — ALLOW
    """

    def evaluate(
        self,
        detection_result: DetectionResult,
        profile: PolicyProfile,
    ) -> EnforcementDecision:
        """Produce an EnforcementDecision for the given findings and profile.

        The evaluator picks the right category-action map based on
        ``detection_result.scanned_direction``:

        - ``"input"`` (or anything other than "output") → uses
          ``profile.category_actions``.
        - ``"output"`` → uses ``profile.category_actions_output`` if the
          profile has set it, otherwise falls back to
          ``profile.category_actions`` (preserves pre-Fix-3 behavior
          for profiles that don't define a separate outbound dict).

        ``severity_threshold_block`` and the ALLOW fallback are
        unchanged — they apply identically to both directions.
        """
        if not detection_result.has_sensitive_content:
            return EnforcementDecision(
                match_actions=[],
                policy_profile_id=profile.profile_id,
                reasons=["No sensitive content detected."],
            )

        # Resolve the active category-action map for this scan's direction
        # ONCE, then pass it to _decide for every match — keeps the
        # decision logic stateless and avoids re-checking direction in
        # every loop iteration.
        is_output = detection_result.scanned_direction == "output"
        if is_output and profile.category_actions_output is not None:
            category_actions = profile.category_actions_output
        else:
            category_actions = profile.category_actions

        match_actions: list[MatchAction] = []
        reasons: list[str] = []

        for match in detection_result.matches:
            action, reason_code, reason_message = self._decide(
                match, profile, category_actions
            )
            match_actions.append(
                MatchAction(
                    match=match,
                    action=action,
                    reason_code=reason_code,
                    reason_message=reason_message,
                )
            )

        # Build aggregated reasons from unique reason messages
        seen_reasons: set[str] = set()
        for ma in match_actions:
            if ma.reason_message not in seen_reasons:
                reasons.append(ma.reason_message)
                seen_reasons.add(ma.reason_message)

        return EnforcementDecision(
            match_actions=match_actions,
            policy_profile_id=profile.profile_id,
            reasons=reasons,
        )

    @staticmethod
    def _decide(
        match: DetectionMatch,
        profile: PolicyProfile,
        category_actions: dict[str, EnforcementAction],
    ) -> tuple[EnforcementAction, str, str]:
        """Decide the action for a single match.

        Returns (action, reason_code, reason_message). ``category_actions``
        is the direction-resolved map chosen by ``evaluate`` — pass
        ``profile.category_actions`` for input scans or
        ``profile.category_actions_output`` (when set) for output scans.
        """
        # Check 1: explicit category action from profile (direction-resolved).
        # This is the operator-visible policy surface in the Admin UI, so it
        # must win over the generic severity fallback. A category configured
        # to BLOCK still blocks; MASK/ALLOW do not get escalated just because
        # a detector assigned CRITICAL severity.
        category_action = category_actions.get(match.category)
        if category_action is not None:
            action_name = category_action.name.lower()
            return (
                category_action,
                f"{match.category}_{action_name}_policy",
                f"Category '{match.category}' is configured to {action_name} "
                f"by profile '{profile.name}'.",
            )

        # Check 2: severity threshold block for uncategorized findings.
        if (
            profile.severity_threshold_block is not None
            and match.severity >= profile.severity_threshold_block
        ):
            return (
                EnforcementAction.BLOCK,
                "severity_threshold",
                f"Finding severity {match.severity.name} meets or exceeds "
                f"block threshold {profile.severity_threshold_block.name}.",
            )

        # Check 3: fallback — allow
        return (
            EnforcementAction.ALLOW,
            "no_matching_rule",
            f"No policy rule matched for category '{match.category}'.",
        )
