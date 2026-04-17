"""Content guardrails — pre-provider regex blocklist.

Blocks jailbreak attempts, prompt injection markers, and harmful synthesis
requests before the request ever reaches an upstream model. Pattern-based
(zero cost, sub-millisecond per call).

Guardrails are distinct from DLP:
- DLP is about preventing *data leakage* (PII, secrets) — masks or blocks
  based on what the user is sending OUT.
- Guardrails are about *content safety* — refusing to relay dangerous
  instructions to the model in the first place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


RULES: dict[str, list[str]] = {
    "jailbreak_dan": [
        r"you are now (?:DAN|an AI without restrictions|jailbroken)",
        r"pretend you (?:have no|don't have) (?:rules|restrictions|guidelines|limits)",
        r"act as (?:if you were trained|an unrestricted|a different AI)",
        r"(?:ignore|disregard|forget)(?:\s+(?:all|your|previous|the))*\s+(?:instructions|rules|guidelines|restrictions|system\s+prompt)",
        r"you (?:have no|must have no) (?:ethical )?(?:restrictions|limits|guidelines)",
    ],
    "prompt_injection": [
        r"new (?:system )?(?:instruction|prompt|directive|command):",
        r"<\|(?:system|endoftext|im_start|im_end)\|>",
        r"\[SYSTEM\]|\[INST\]|\[/INST\]",
        r"###\s*(?:System|Instruction|Human|Assistant):",
    ],
    "harmful_synthesis": [
        r"(?:how to|steps to|guide (?:me |us )?to|instructions? for)\b.{0,40}\b(?:make|build|create|synthesize|manufacture)\b.{0,40}\b(?:bomb|explosive|weapon|poison|nerve agent|bioweapon)",
        r"(?:synthesis|synthesize|produce|manufacture)\b.{0,30}\b(?:methamphetamine|fentanyl|sarin|VX gas|ricin)",
    ],
}


_COMPILED: list[tuple[str, re.Pattern[str]]] = [
    (rule_name, re.compile(pattern, re.IGNORECASE | re.DOTALL))
    for rule_name, patterns in RULES.items()
    for pattern in patterns
]


@dataclass(frozen=True)
class GuardrailResult:
    passed: bool
    reason: str | None = None
    rule_name: str | None = None


class GuardrailService:
    """Stateless regex-based content guardrail."""

    def check(self, messages: list[dict]) -> GuardrailResult:
        """Scan user/system message text for blocked patterns.

        Assistant messages are ignored — guardrails protect against the user
        injecting harmful content, not against the model echoing it back (that
        is the DLP output scanner's job).
        """
        text_parts: list[str] = []
        for m in messages:
            if not isinstance(m, dict):
                continue
            if m.get("role") not in ("user", "system"):
                continue
            content = m.get("content")
            if isinstance(content, str):
                text_parts.append(content)
        text = " ".join(text_parts)

        for rule_name, pattern in _COMPILED:
            if pattern.search(text):
                return GuardrailResult(
                    passed=False,
                    reason="Request blocked by content guardrail",
                    rule_name=rule_name,
                )

        return GuardrailResult(passed=True)
