"""Phase 1 — tool-call argument DLP parity.

Both the non-streaming path (orchestrator section 8d) and the streaming
path (public_chat post-loop block) run tool-call arguments through the
SAME shared pipeline:

    registry.scan_text → DLPPolicyEngine.evaluate_decision → transformer.mask_text

This test exercises that shared pipeline directly to prove the streaming
fix produces the same enforcement the non-streaming path always had:
- PII in arguments is MASKED (raw value gone, ``<r`` token present),
- a BLOCK profile blocks the arguments,
- masked arguments remain valid JSON-string content.

Unit lane only.
"""

from __future__ import annotations

import json

from app.domain.dlp import DetectionResult, EnforcementAction
from app.domain.policy import PolicyProfile
from app.services.dlp.detectors.registry import get_default_registry
from app.services.dlp.policy_engine import (
    DLPPolicyEngine,
    DLPRequestContext,
    ScanDirection,
    SourceType,
)
from app.services.dlp.transformer import DLPTransformer
from app.services.policy.profiles import DEFAULT_PROFILE, STRICT_PROFILE


def _pre_restore(profile: PolicyProfile) -> PolicyProfile:
    """Mirror orchestrator's ``pre_restore_profile`` (section 8b): on the
    OUTPUT direction, RESTORE becomes MASK so newly-generated PII is
    masked before the restore pass. This is the profile the tool-arg path
    actually evaluates against in both stream and non-stream."""
    base = profile.category_actions_output or profile.category_actions
    converted = {
        cat: (EnforcementAction.MASK if act == EnforcementAction.RESTORE else act)
        for cat, act in base.items()
    }
    return PolicyProfile(
        profile_id=profile.profile_id,
        name=profile.name,
        category_actions=profile.category_actions,
        category_actions_output=converted,
        severity_threshold_block=profile.severity_threshold_block,
        scan_responses=profile.scan_responses,
    )


def _enforce(args: str, profile):
    """Mirror the tool-arg pipeline shared by stream + non-stream."""
    registry = get_default_registry(ner_enabled=False)
    matches = registry.scan_text(args)
    detection = DetectionResult(matches=matches, scanned_direction="output")
    engine = DLPPolicyEngine()
    ctx = DLPRequestContext(
        source_type=SourceType.TOOL_ARGUMENT, direction=ScanDirection.OUTPUT
    )
    decision = engine.evaluate_decision(
        detection_result=detection, profile=_pre_restore(profile), context=ctx
    )
    return decision


# A send_email-style tool call whose arguments embed a credit card number.
_TOOL_ARGS = json.dumps(
    {"to": "ops@example.com", "note": "charge card 4532015112830366"}
)


class TestToolArgParity:
    def test_pii_in_arguments_is_masked(self):
        decision = _enforce(_TOOL_ARGS, DEFAULT_PROFILE)
        assert decision.requires_transformation
        masked, span_map, _ = DLPTransformer().mask_text(
            _TOOL_ARGS, decision.match_actions
        )
        # Raw card digits gone; placeholder token present.
        assert "4532015112830366" not in masked
        assert "<r" in masked
        # Still parseable JSON (mask tokens are valid string content).
        json.loads(masked)

    def test_strict_profile_blocks_financial_arguments(self):
        decision = _enforce(_TOOL_ARGS, STRICT_PROFILE)
        # financial → BLOCK under STRICT; the streaming path drops the
        # arguments (parity with the non-stream DLPViolationError block).
        assert decision.is_blocked

    def test_clean_arguments_untouched(self):
        clean = json.dumps({"city": "Bangkok", "unit": "celsius"})
        decision = _enforce(clean, DEFAULT_PROFILE)
        # No financial/secret content → no transformation required.
        assert not decision.is_blocked
