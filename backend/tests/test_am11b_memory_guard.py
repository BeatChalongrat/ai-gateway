"""AM11b — the memory guard, for Agent Market's own services.

``test_memory_guard_compose.py`` holds the platform's compose files to a ceiling, a turn in the
out-of-memory order and the databases last. Agent Market runs three more live containers from its own
compose file; the same rules hold for them, and they give way before the platform's applications: the
runner at -500, the document converter and the web fetcher at -300.
"""

from __future__ import annotations

import pytest
import yaml

from tests.test_memory_guard_compose import ROOT, VM, _services

OURS = {"docker-compose.agent-market.yml": ["agent-runner", "doc-converter", "web-fetcher"]}

pytestmark = pytest.mark.skipif(not all((ROOT / f).is_file() for f in OURS), reason="the compose file is not reachable from this lane")


def test_each_has_a_ceiling_and_its_turn():
    for file, name, m in _services(OURS):
        assert 0 < m["ceiling"] <= VM, f"{file}:{name} has no ceiling"
        assert isinstance(m["turn"], int) and -1000 < m["turn"] < 0, f"{file}:{name} has no turn below a gate's"


def test_they_give_way_before_the_platforms_applications_and_long_before_a_database():
    turns = {name: m["turn"] for _, name, m in _services(OURS)}
    assert turns == {"agent-runner": -500, "doc-converter": -300, "web-fetcher": -300}
    assert max(turns.values()) < 0 and min(turns.values()) > -700


def test_the_test_project_runs_the_same_three_and_there_they_go_first():
    """The test stack starts these three from the same compose file, with scripts/agent-market/test-web.yml
    laid over it. Without a word there they would be as protected as live's own, in the same VM."""
    override = ROOT / "scripts/agent-market/test-web.yml"
    if not override.is_file():
        pytest.skip("the test project's override is not reachable from this lane")
    over = (yaml.safe_load(override.read_text(encoding="utf-8")) or {}).get("services") or {}
    for name in OURS["docker-compose.agent-market.yml"]:
        turn = (over.get(name) or {}).get("oom_score_adj")
        assert isinstance(turn, int) and turn > 0, f"the test project's {name} keeps live's turn ({turn})"
