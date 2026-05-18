"""Batch-1 parity validation against the live Python registry.

The fixtures in ``dlp-sidecar/tests/parity/fixtures/`` are the
contract Rust must reproduce. This test re-runs every case through
the **Python** registry and asserts the same (count, spans) the
fixture pins. It is the safety net that catches a stale fixture or
a Python detector behaviour drift before the Rust port lands.

When the Rust sidecar is wired into the orchestrator (Batch 4), an
additional parity test will run the same inputs through the gRPC
client and assert Python ↔ Rust output is identical at the
DetectionMatch level. For Batch 1 we only validate fixtures.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from app.services.dlp.detectors.bearer_token import BearerTokenDetector
from app.services.dlp.detectors.email import EmailDetector
from app.services.dlp.detectors.iban import IBANDetector
from app.services.dlp.detectors.ip_address import IPAddressDetector
from app.services.dlp.detectors.line_id import LineIdDetector
from app.services.dlp.detectors.mac_address import MacAddressDetector
from app.services.dlp.detectors.password_field import PasswordFieldDetector
from app.services.dlp.detectors.private_key import PrivateKeyDetector
from app.services.dlp.detectors.ssn import SSNDetector


# In the dev container the repo is mounted at /app; on host CI the
# fixtures live two levels up from this file. Try both.
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
    "email": EmailDetector(),
    "ssn": SSNDetector(),
    "iban": IBANDetector(),
    "ip_address": IPAddressDetector(),
    "mac_address": MacAddressDetector(),
    "bearer_token": BearerTokenDetector(),
    "private_key": PrivateKeyDetector(),
    "password_field": PasswordFieldDetector(),
    "line_id": LineIdDetector(),
}


def _load_fixture(name: str) -> dict:
    if FIXTURES_DIR is None:
        pytest.skip("dlp-sidecar fixtures dir not mounted in this env")
    path = FIXTURES_DIR / f"{name}.json"
    if not path.exists():
        pytest.skip(f"fixture {name}.json not yet ported")
    return json.loads(path.read_text())


@pytest.mark.parametrize("detector_name", sorted(_DETECTORS))
def test_python_matches_batch1_fixture(detector_name):
    """Every fixture case must agree with the live Python detector."""
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
