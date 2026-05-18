"""Batch-4 parity validation — thai_name, credit_card, passport.

Same shape as batches 1-3."""

from __future__ import annotations

import json
import pathlib

import pytest

from app.services.dlp.detectors.credit_card import CreditCardDetector
from app.services.dlp.detectors.passport import PassportDetector
from app.services.dlp.detectors.thai_name import ThaiNameDetector
from app.services.dlp.detectors.thai_name_no_prefix import (
    ThaiNameNoPrefixDetector,
)


_HERE = pathlib.Path(__file__).resolve()
_CANDIDATES = [
    _HERE.parent.parent.parent / "dlp-sidecar" / "tests" / "parity" / "fixtures",
    pathlib.Path("/work/dlp-sidecar/tests/parity/fixtures"),
]
FIXTURES_DIR: pathlib.Path | None = None
for c in _CANDIDATES:
    if c.exists():
        FIXTURES_DIR = c
        break


_DETECTORS = {
    "thai_name": ThaiNameDetector(),
    "thai_name_no_prefix": ThaiNameNoPrefixDetector(),
    "credit_card": CreditCardDetector(),
    "passport": PassportDetector(),
}


def _load_fixture(name: str) -> dict:
    if FIXTURES_DIR is None:
        pytest.skip("dlp-sidecar fixtures dir not mounted in this env")
    path = FIXTURES_DIR / f"{name}.json"
    if not path.exists():
        pytest.skip(f"fixture {name}.json not yet ported")
    return json.loads(path.read_text())


@pytest.mark.parametrize("detector_name", sorted(_DETECTORS))
def test_python_matches_batch4_fixture(detector_name):
    data = _load_fixture(detector_name)
    detector = _DETECTORS[detector_name]
    for case in data["cases"]:
        matches = detector.detect(case["input"])
        assert len(matches) == case["expected_count"], (
            f"[{detector_name}/{case['id']}] expected "
            f"{case['expected_count']} matches, got {len(matches)} — "
            f"input={case['input']!r}"
        )
        for i, span in enumerate(case["expected_spans"]):
            assert matches[i].start == span["start"], (
                f"[{detector_name}/{case['id']}] span[{i}].start "
                f"expected {span['start']}, got {matches[i].start}"
            )
            assert matches[i].end == span["end"], (
                f"[{detector_name}/{case['id']}] span[{i}].end "
                f"expected {span['end']}, got {matches[i].end}"
            )
