"""Sensitivity classifier for DLP detection matches.

Sits between detection and policy evaluation in the pipeline:

    Detectors → [raw matches] → **Classifier** → [classified matches] → Evaluator

Two responsibilities:
1. Provide an authoritative mapping of entity types → default severity
   (``DEFAULT_SEVERITY_MAP``).
2. Apply cross-finding escalation rules that adjust severity based on
   the combination of findings in a single scan.

Design constraints:
- Pure function: classify() takes matches in, returns new matches out.
- No mutation of input (DetectionMatch is frozen; new instances are created).
- No policy decisions (no allow/mask/block logic).
- No tenant-specific overrides (those belong in policy profiles).
"""

from __future__ import annotations

from dataclasses import replace

from app.domain.dlp import DetectionMatch, SensitivityLevel

# ---------------------------------------------------------------------------
# Authoritative entity-type → default severity mapping
# ---------------------------------------------------------------------------

DEFAULT_SEVERITY_MAP: dict[str, SensitivityLevel] = {
    # PII detectors
    "email_address": SensitivityLevel.MEDIUM,
    "phone_number": SensitivityLevel.HIGH,
    "us_ssn": SensitivityLevel.HIGH,
    "date_of_birth": SensitivityLevel.HIGH,
    "vehicle_plate": SensitivityLevel.MEDIUM,
    # Secret detectors
    "bearer_token": SensitivityLevel.CRITICAL,
    "aws_access_key": SensitivityLevel.CRITICAL,
    "openai_api_key": SensitivityLevel.CRITICAL,
    "github_token": SensitivityLevel.CRITICAL,
    "slack_token": SensitivityLevel.CRITICAL,
    "private_key_header": SensitivityLevel.CRITICAL,
    "password_in_config": SensitivityLevel.HIGH,
    # NER entities (Presidio). Person is the only one we treat as
    # MEDIUM by default — locations / organizations / GPE / NRP are
    # LOW because they're frequently non-sensitive common nouns
    # ("Bangkok", "Google") that policies often want to allow.
    "person": SensitivityLevel.MEDIUM,
    "location": SensitivityLevel.LOW,
    "organization": SensitivityLevel.LOW,
    "gpe": SensitivityLevel.LOW,
    "nrp": SensitivityLevel.LOW,
    # DLP coverage expansion — Thai + extras.
    "thai_national_id": SensitivityLevel.HIGH,
    "thai_tax_id": SensitivityLevel.HIGH,
    "thai_bank_account": SensitivityLevel.HIGH,
    "thai_promptpay": SensitivityLevel.MEDIUM,
    "thai_address": SensitivityLevel.MEDIUM,
    "address": SensitivityLevel.MEDIUM,
    "income": SensitivityLevel.HIGH,
    "db_connection_string": SensitivityLevel.CRITICAL,
    "medical_record": SensitivityLevel.HIGH,
    "mac_address": SensitivityLevel.LOW,
    # PDPA Section 26 sensitive data.
    "religion_belief": SensitivityLevel.HIGH,
    "health_condition": SensitivityLevel.HIGH,
    "criminal_record": SensitivityLevel.HIGH,
    "biometric_data": SensitivityLevel.HIGH,
}

# Minimum escalation threshold — if triggered, everything below this
# gets raised to this level.
_ESCALATION_FLOOR = SensitivityLevel.HIGH


class SensitivityClassifier:
    """Classifies and optionally escalates detection match severities.

    Parameters
    ----------
    severity_map : dict[str, SensitivityLevel] | None
        Override the default entity-type → severity mapping.
        If None, uses ``DEFAULT_SEVERITY_MAP``.
    """

    def __init__(
        self,
        severity_map: dict[str, SensitivityLevel] | None = None,
    ) -> None:
        self._map = severity_map or DEFAULT_SEVERITY_MAP

    def baseline_severity(self, entity_type: str) -> SensitivityLevel:
        """Look up the default severity for an entity type.

        Returns the detector's own severity if the entity type is not
        in the map (forward-compatible with future detectors).
        """
        return self._map.get(entity_type, SensitivityLevel.MEDIUM)

    def classify(self, matches: list[DetectionMatch]) -> list[DetectionMatch]:
        """Return a new list of matches with severity potentially escalated.

        Escalation rules (MVP):
        1. If matches span 2+ distinct categories → escalate all below
           HIGH to HIGH.
        2. If 3+ distinct entity types are present → escalate all below
           HIGH to HIGH.

        Matches already at HIGH or CRITICAL are never downgraded.
        """
        if not matches:
            return []

        escalate = self._should_escalate(matches)

        result: list[DetectionMatch] = []
        for m in matches:
            new_severity = m.severity
            if escalate and new_severity < _ESCALATION_FLOOR:
                new_severity = _ESCALATION_FLOOR
            if new_severity != m.severity:
                m = replace(m, severity=new_severity)
            result.append(m)

        return result

    @staticmethod
    def _should_escalate(matches: list[DetectionMatch]) -> bool:
        """Determine if cross-finding escalation should trigger."""
        categories = {m.category for m in matches}
        entity_types = {m.entity_type for m in matches}
        return len(categories) >= 2 or len(entity_types) >= 3
