"""Batch-2 parity validation against the live Python registry.

Same shape as ``test_dlp_parity_batch1.py`` — the fixture JSONs at
``dlp-sidecar/tests/parity/fixtures/`` are the contract Rust must
reproduce, and this test re-runs each case through the live Python
detector to assert the fixture stays in sync with Python output.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from app.services.dlp.detectors.address import AddressDetector
from app.services.dlp.detectors.api_key_secret import APIKeySecretDetector
from app.services.dlp.detectors.biometric_data import BiometricDataDetector
from app.services.dlp.detectors.criminal_record import CriminalRecordDetector
from app.services.dlp.detectors.date_of_birth import DateOfBirthDetector
from app.services.dlp.detectors.db_connection_string import (
    DbConnectionStringDetector,
)
from app.services.dlp.detectors.health_condition import HealthConditionDetector
from app.services.dlp.detectors.income import IncomeDetector
from app.services.dlp.detectors.medical_record import MedicalRecordDetector
from app.services.dlp.detectors.phone import PhoneDetector
from app.services.dlp.detectors.religion_belief import ReligionBeliefDetector
from app.services.dlp.detectors.thai_address import ThaiAddressDetector
from app.services.dlp.detectors.vehicle_plate import VehiclePlateDetector


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
    "phone": PhoneDetector(),
    "api_key_secret": APIKeySecretDetector(),
    "db_connection_string": DbConnectionStringDetector(),
    "medical_record": MedicalRecordDetector(),
    "date_of_birth": DateOfBirthDetector(),
    "vehicle_plate": VehiclePlateDetector(),
    "address": AddressDetector(),
    "thai_address": ThaiAddressDetector(),
    "income": IncomeDetector(),
    "religion_belief": ReligionBeliefDetector(),
    "health_condition": HealthConditionDetector(),
    "criminal_record": CriminalRecordDetector(),
    "biometric_data": BiometricDataDetector(),
}


def _load_fixture(name: str) -> dict:
    if FIXTURES_DIR is None:
        pytest.skip("dlp-sidecar fixtures dir not mounted in this env")
    path = FIXTURES_DIR / f"{name}.json"
    if not path.exists():
        pytest.skip(f"fixture {name}.json not yet ported")
    return json.loads(path.read_text())


@pytest.mark.parametrize("detector_name", sorted(_DETECTORS))
def test_python_matches_batch2_fixture(detector_name):
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
