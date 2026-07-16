"""FIX 6 / Bug 4 — the sk-/gw_ credential families are matched on their
PREFIX (structured credential), not a 20-char length threshold, so a
short-but-real key (sk-test-abc123) no longer egresses raw. A benign short
string without the prefix is not falsely flagged.

Mirrors the Rust sidecar change (dlp-sidecar/src/detectors/bearer_token.rs);
the DLP parity suite pins the two engines in lockstep.
"""

from __future__ import annotations

import pytest

from app.services.dlp.detectors.bearer_token import BearerTokenDetector

DET = BearerTokenDetector()


def _entities(text: str) -> list[str]:
    return [m.entity_type for m in DET.detect(text)]


@pytest.mark.parametrize(
    "text, entity",
    [
        ("my key is sk-test-abc123 ok", "openai_api_key"),   # SHORT real key (the leak)
        ("token sk-proj-abcdef here", "openai_api_key"),      # short project key
        ("leaked gw_abc123def456 oops", "gateway_api_key"),   # gateway key
        ("use sk-proj-1a2b3c4d5e6f7g8h9i0jKLMNOP12345", "openai_api_key"),  # long still works
    ],
)
def test_prefix_credentials_are_detected(text, entity):
    assert entity in _entities(text), f"{text!r} should flag {entity}"


def test_category_is_secret():
    ms = DET.detect("key sk-test-abc123")
    assert ms and all(m.category == "secret" for m in ms)


@pytest.mark.parametrize(
    "benign",
    [
        "the desk is blue and small",   # 'desk' contains 'sk' but no sk- token
        "she asked him a question",     # 'asked' -> no \bsk- boundary
        "hello world nothing secret",
        "gateway status is green",      # 'gw' word but no gw_ token
    ],
)
def test_benign_short_strings_not_flagged(benign):
    assert _entities(benign) == [], f"{benign!r} must not be flagged"


def test_anthropic_still_excluded_from_bearer():
    # sk-ant- is handled by api_key_secret (category credential), not here.
    assert "openai_api_key" not in _entities("key sk-ant-api03-AAAAAAAAAAAAAAAAAAAA")
