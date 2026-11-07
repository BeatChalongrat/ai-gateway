"""P3-12: Validate that example policy profiles match the PolicyProfile dataclass.

Ensures documentation examples stay in sync with the implementation.
"""

import dataclasses
import json
from pathlib import Path

import pytest

from app.domain.dlp import EnforcementAction, SensitivityLevel
from app.domain.policy import PolicyProfile

EXAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "examples"


_POLICY_FILES = [
    "policy_default.json",
    "policy_strict.json",
    "policy_internal_only.json",
]


class TestExampleProfilesExist:
    @pytest.mark.parametrize("filename", _POLICY_FILES)
    def test_file_exists(self, filename):
        path = EXAMPLES_DIR / filename
        assert path.exists(), f"Missing example: {path}"


class TestExampleProfilesParse:
    @pytest.mark.parametrize("filename", _POLICY_FILES)
    def test_valid_json(self, filename):
        path = EXAMPLES_DIR / filename
        data = json.loads(path.read_text())
        assert isinstance(data, dict)

    @pytest.mark.parametrize("filename", _POLICY_FILES)
    def test_has_required_fields(self, filename):
        data = json.loads((EXAMPLES_DIR / filename).read_text())
        required = {"profile_id", "name", "category_actions", "scan_responses"}
        assert required.issubset(data.keys()), (
            f"{filename} missing fields: {required - data.keys()}"
        )

    @pytest.mark.parametrize("filename", _POLICY_FILES)
    def test_category_actions_are_valid(self, filename):
        data = json.loads((EXAMPLES_DIR / filename).read_text())
        valid_actions = {a.name for a in EnforcementAction}
        for category, action in data["category_actions"].items():
            assert action in valid_actions, (
                f"{filename}: invalid action '{action}' for category '{category}'. "
                f"Valid: {valid_actions}"
            )

    @pytest.mark.parametrize("filename", _POLICY_FILES)
    def test_severity_threshold_valid(self, filename):
        data = json.loads((EXAMPLES_DIR / filename).read_text())
        threshold = data.get("severity_threshold_block")
        if threshold is not None:
            valid_levels = {s.name for s in SensitivityLevel}
            assert threshold in valid_levels, (
                f"{filename}: invalid severity '{threshold}'. Valid: {valid_levels}"
            )


class TestExampleProfilesConstructible:
    """Verify each example can construct a valid PolicyProfile."""

    @pytest.mark.parametrize("filename", _POLICY_FILES)
    def test_constructs_policy_profile(self, filename):
        data = json.loads((EXAMPLES_DIR / filename).read_text())

        category_actions = {
            cat: EnforcementAction[action]
            for cat, action in data["category_actions"].items()
        }

        threshold = data.get("severity_threshold_block")
        severity_threshold = SensitivityLevel[threshold] if threshold else None

        profile = PolicyProfile(
            profile_id=data["profile_id"],
            name=data["name"],
            category_actions=category_actions,
            severity_threshold_block=severity_threshold,
            scan_responses=data["scan_responses"],
        )

        assert profile.profile_id == data["profile_id"]
        assert profile.name == data["name"]
        assert profile.scan_responses == data["scan_responses"]


class TestOutdatedSampleRemoved:
    def test_old_sample_policy_profile_deleted(self):
        """The old sample_policy_profile.json with incorrect fields should be gone."""
        old_path = EXAMPLES_DIR / "sample_policy_profile.json"
        assert not old_path.exists(), (
            "sample_policy_profile.json still exists — it has outdated fields "
            "and should have been replaced by policy_default/strict/internal_only.json"
        )


class TestSpecificProfileBehavior:
    """Spot-check that the example profiles encode the intended policies."""

    def test_default_masks_pii(self):
        data = json.loads((EXAMPLES_DIR / "policy_default.json").read_text())
        assert data["category_actions"]["pii"] == "MASK"

    def test_default_blocks_secrets(self):
        data = json.loads((EXAMPLES_DIR / "policy_default.json").read_text())
        assert data["category_actions"]["secret"] == "BLOCK"

    def test_strict_blocks_financial(self):
        data = json.loads((EXAMPLES_DIR / "policy_strict.json").read_text())
        assert data["category_actions"]["financial"] == "BLOCK"

    def test_strict_has_critical_threshold(self):
        data = json.loads((EXAMPLES_DIR / "policy_strict.json").read_text())
        assert data["severity_threshold_block"] == "CRITICAL"

    def test_internal_allows_pii(self):
        data = json.loads((EXAMPLES_DIR / "policy_internal_only.json").read_text())
        assert data["category_actions"]["pii"] == "ALLOW"

    def test_internal_no_response_scanning(self):
        data = json.loads((EXAMPLES_DIR / "policy_internal_only.json").read_text())
        assert data["scan_responses"] is False
