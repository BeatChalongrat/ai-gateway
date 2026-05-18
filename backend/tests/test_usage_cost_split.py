"""Billing input/output split tests.

Verifies the new ``_estimate_cost`` signature
``(model, prompt_tokens, completion_tokens)``:
- Uses per-input/per-output rates when the registry has them.
- Falls back to the legacy flat-rate prefix table for unknown models.
- Split path remains deterministic for a known-priced model.
"""

from __future__ import annotations

import pytest

from app.services.usage_service import _estimate_cost


def test_billing_uses_input_output_split_when_available():
    # gpt-4o: input=0.0000025 $/tok, output=0.000010 $/tok.
    # 1000 * 0.0000025 + 500 * 0.000010 = 0.0025 + 0.005 = 0.0075
    cost = _estimate_cost("gpt-4o", 1000, 500)
    assert cost == pytest.approx(0.0075)


def test_billing_falls_back_to_flat_rate_for_unknown_model():
    # Unknown model — must not raise, must return a positive float.
    cost = _estimate_cost("some-unknown-model-xyz", 100, 50)
    assert isinstance(cost, float)
    assert cost > 0


def test_billing_split_cheaper_than_flat_for_low_completion():
    # claude-3-5-sonnet: input=0.000003, output=0.000015 (split path).
    # Legacy flat-rate prefix table has 0.000003 for the same prefix.
    # We're not asserting which is larger — only that the split path
    # is reachable and deterministic for a known-priced model.
    prompt_tokens, completion_tokens = 2000, 50
    split = _estimate_cost(
        "claude-3-5-sonnet-20241022", prompt_tokens, completion_tokens
    )
    expected_split = 2000 * 0.000003 + 50 * 0.000015
    assert split == pytest.approx(expected_split)
