"""Cost-tier derivation — the single home for the price thresholds.

Previously duplicated verbatim in ``model_registry._derive_cost_tier``
and ``discovery_service._derive_cost_tier`` (the latter to dodge an
import cycle through the registry's lifespan wiring). Both now import
from this leaf module, which imports nothing from the app, so neither
direction can cycle.
"""

from __future__ import annotations

# USD per input token. <= BUDGET_MAX → budget; <= STANDARD_MAX →
# standard; above → premium. 0.0 (unknown/free — e.g. a self-hosted
# box) deliberately maps to "standard", not "budget": an unknown price
# must not win cost-based routing.
BUDGET_MAX_COST_PER_INPUT_TOKEN = 0.0000005
STANDARD_MAX_COST_PER_INPUT_TOKEN = 0.000003


def derive_cost_tier(price: float) -> str:
    if price == 0.0:
        return "standard"
    if price <= BUDGET_MAX_COST_PER_INPUT_TOKEN:
        return "budget"
    if price <= STANDARD_MAX_COST_PER_INPUT_TOKEN:
        return "standard"
    return "premium"
