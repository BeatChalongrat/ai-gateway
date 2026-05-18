"""Batch-3 parity validation — checksum-based detectors.

Same shape as test_dlp_parity_batch{1,2}.py. The fixtures here cover
checksum-validated 13-digit Thai IDs (national / tax / promptpay)
and the keyword-gated bank account detector.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from app.services.dlp.detectors.thai_bank_account import ThaiBankAccountDetector
from app.services.dlp.detectors.thai_national_id import ThaiNationalIdDetector
from app.services.dlp.detectors.thai_promptpay import ThaiPromptPayDetector
from app.services.dlp.detectors.thai_tax_id import ThaiTaxIdDetector


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
    "thai_national_id": ThaiNationalIdDetector(),
    "thai_tax_id": ThaiTaxIdDetector(),
    "thai_bank_account": ThaiBankAccountDetector(),
    "thai_promptpay": ThaiPromptPayDetector(),
}


def _load_fixture(name: str) -> dict:
    if FIXTURES_DIR is None:
        pytest.skip("dlp-sidecar fixtures dir not mounted in this env")
    path = FIXTURES_DIR / f"{name}.json"
    if not path.exists():
        pytest.skip(f"fixture {name}.json not yet ported")
    return json.loads(path.read_text())


@pytest.mark.parametrize("detector_name", sorted(_DETECTORS))
def test_python_matches_batch3_fixture(detector_name):
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
