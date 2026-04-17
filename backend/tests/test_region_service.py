"""P10-07: Regional / residency control tests."""

from app.services.region_service import (
    RegionConstraints, get_blocked_providers_for_region,
    get_region_constraints, list_regions,
)


class TestRegionConstraints:
    def test_global_unconstrained(self):
        c = get_region_constraints("global")
        assert c.allowed_providers is None
        assert c.blocked_providers is None

    def test_eu_blocks_openrouter(self):
        c = get_region_constraints("eu")
        assert c.blocked_providers is not None
        assert "openrouter" in c.blocked_providers

    def test_us_unconstrained(self):
        c = get_region_constraints("us")
        assert c.blocked_providers is None

    def test_unknown_region_unconstrained(self):
        c = get_region_constraints("unknown-region")
        assert c.allowed_providers is None

    def test_blocked_providers_helper(self):
        blocked = get_blocked_providers_for_region("eu")
        assert blocked is not None
        assert "openrouter" in blocked

    def test_list_regions(self):
        regions = list_regions()
        assert len(regions) >= 3
        names = {r["region"] for r in regions}
        assert "global" in names
        assert "eu" in names

    def test_frozen(self):
        import pytest
        c = RegionConstraints(region="test")
        with pytest.raises(AttributeError):
            c.region = "x"  # type: ignore[misc]
