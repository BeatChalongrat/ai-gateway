"""Shared helpers for the DLP detector / pipeline / regression suites.

Provides a minimal mask/restore wrapper around the full DLP pipeline so
each per-detector test file can read like:

    from backend.tests.test_dlp_detectors._helpers import mask, restore

    masked, span_map, matches = mask("Email user@example.com")
    assert any("EMAIL" in t for t in span_map)
    assert restore(masked, span_map) == "Email user@example.com"

The pipeline used here is intentionally the same one the orchestrator
runs in production:

    DetectorRegistry → SensitivityClassifier → PolicyEvaluator → DLPTransformer

with the built-in ``default`` profile so MASK is the default action for
every supported category. Tests that want to assert raw detection
(``did detector X fire on this span?``) call ``scan_only`` instead — it
returns the unfiltered list of ``DetectionMatch`` objects so a single
detector can be inspected without policy interference.
"""

from __future__ import annotations

from app.schemas.common import Message
from app.services.dlp.classifier import SensitivityClassifier
from app.services.dlp.detectors.registry import get_default_registry
from app.services.dlp.transformer import DLPTransformer
from app.services.policy.evaluator import PolicyEvaluator
from app.services.policy.profiles import get_profile

# Registry / pipeline objects are stateless and can be reused safely
# across calls. Building them once at import shaves ~50ms off every
# test that calls mask() — meaningful when the suite has 100+ such
# calls.
_REGISTRY = get_default_registry()
_CLASSIFIER = SensitivityClassifier()
_EVALUATOR = PolicyEvaluator()
_TRANSFORMER = DLPTransformer()
_DEFAULT_PROFILE = get_profile("default")


def mask(text: str) -> tuple[str, dict[str, str], list]:
    """Run the full DLP pipeline against *text* and return masking artefacts.

    Returns
    -------
    (masked_text, span_map, match_actions)
        ``masked_text`` — input with PII replaced by indexed mask tokens.
        ``span_map`` — token → original-value mapping suitable for
        :func:`restore`.
        ``match_actions`` — the list of ``MatchAction`` objects the
        evaluator produced. Tests can introspect this list to assert
        which detector fired (and what its category / action were).
    """
    messages = [Message(role="user", content=text)]
    detection = _REGISTRY.scan_messages(messages, direction="input")
    if detection.has_sensitive_content:
        detection.matches = _CLASSIFIER.classify(detection.matches)
    enforcement = _EVALUATOR.evaluate(detection, _DEFAULT_PROFILE)
    new_msgs, span_map, _ = _TRANSFORMER.mask_messages(
        messages, enforcement.match_actions
    )
    return new_msgs[0].content, span_map, enforcement.match_actions


def restore(masked: str, span_map: dict[str, str]) -> str:
    """Reverse :func:`mask` — replace every token in *masked* with its original."""
    return _TRANSFORMER.restore(masked, span_map)


def scan_only(text: str, *, language: str = "en") -> list:
    """Run detectors only — no classifier, no policy, no transform.

    Useful for "did detector X fire on span Y" assertions where the
    policy outcome is irrelevant.
    """
    return _REGISTRY.scan_text(text, language=language)


def detector_names_for(text: str) -> set[str]:
    """Return the set of detector names that fired on *text*."""
    return {m.detector_name for m in scan_only(text)}


def has_match(match_actions, *detector_names: str) -> bool:
    """Return True iff any MatchAction names one of *detector_names*."""
    wanted = set(detector_names)
    return any(ma.match.detector_name in wanted for ma in match_actions)


def matches_for(match_actions, *detector_names: str) -> list:
    """Return MatchActions whose detector name is in *detector_names*."""
    wanted = set(detector_names)
    return [ma for ma in match_actions if ma.match.detector_name in wanted]


def tokens_in(
    span_map: dict[str, str],
    base: str,
    match_actions: list | None = None,
) -> list[str]:
    """Return mask tokens minted by detectors whose mask-token base equals *base*.

    Example::

        tokens_in(span_map, "EMAIL", matches) -> ["<r0>", "<r1>"]

    Lets a test assert "exactly N emails were masked" without having to
    enumerate every counter value the pipeline might mint.

    Under the neutral ``<rN>`` token shape the category is no longer
    embedded in the token itself, so this helper recovers it by walking
    the ``match_actions`` list in start-position order — the same order
    the transformer uses to advance its global counter. Passing
    ``match_actions`` is required; the legacy two-arg signature still
    works for span_maps containing legacy ``[CATEGORY_N]`` tokens
    (transitional support; will be removed once no callers depend on
    it).
    """
    if match_actions is None:
        prefix_legacy = f"[{base}_"
        return [t for t in span_map if t.startswith(prefix_legacy)]

    from app.domain.dlp import EnforcementAction
    from app.services.dlp.transformer import _mask_base_name

    actionable = sorted(
        (
            ma for ma in match_actions
            if ma.action in (EnforcementAction.MASK, EnforcementAction.REDACT)
        ),
        key=lambda ma: ma.match.start,
    )
    result: list[str] = []
    idx = 0
    for ma in actionable:
        if ma.action != EnforcementAction.MASK:
            # REDACT entries don't consume a counter slot.
            continue
        if _mask_base_name(ma.match.detector_name) == base:
            result.append(f"<r{idx}>")
        idx += 1
    # The transformer dedups same-value re-mints in deterministic mode
    # but not in counter mode, so per-action enumeration matches the
    # tokens actually present in span_map for the cases unit tests use.
    return [t for t in result if t in span_map]
