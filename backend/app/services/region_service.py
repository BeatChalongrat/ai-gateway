"""Regional / data residency control service.

Maps tenant regions to provider/model constraints for data residency
compliance. When a tenant has a non-global region, certain providers or
models may be disallowed.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RegionConstraints:
    """Provider/model constraints for a given region."""

    region: str
    allowed_providers: frozenset[str] | None = None  # None = all allowed
    blocked_providers: frozenset[str] | None = None
    notes: str = ""


# Region → constraints mapping
_REGION_POLICIES: dict[str, RegionConstraints] = {
    "global": RegionConstraints(region="global"),
    "eu": RegionConstraints(
        region="eu",
        blocked_providers=frozenset({"openrouter"}),
        notes="EU data residency: avoid third-party routing",
    ),
    "us": RegionConstraints(region="us"),
}


def get_region_constraints(region: str) -> RegionConstraints:
    """Get constraints for a tenant region."""
    return _REGION_POLICIES.get(region, RegionConstraints(region=region))


def get_allowed_providers_for_region(region: str) -> frozenset[str] | None:
    """Return the set of allowed providers for a region, or None if unconstrained."""
    constraints = get_region_constraints(region)
    return constraints.allowed_providers


def get_blocked_providers_for_region(region: str) -> frozenset[str] | None:
    """Return the set of blocked providers for a region, or None if unconstrained."""
    constraints = get_region_constraints(region)
    return constraints.blocked_providers


def list_regions() -> list[dict]:
    """List all known regions with their constraints."""
    return [
        {
            "region": c.region,
            "allowed_providers": list(c.allowed_providers) if c.allowed_providers else None,
            "blocked_providers": list(c.blocked_providers) if c.blocked_providers else None,
            "notes": c.notes,
        }
        for c in _REGION_POLICIES.values()
    ]
