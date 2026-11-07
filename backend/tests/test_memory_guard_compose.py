"""The memory guard — every live service says what it may take and whose turn it is.

Live and everything a gate starts share one Docker VM on the host (12 GB of 32). Before 2026-10-07
nothing in the compose files said who gives way when that VM runs out: 20 of the 27 live containers had
no ceiling, none had a place in the out-of-memory order, and a gate (test stack, sandboxes, image
builds, a desktop build) pushed the host to 1.1 GB free five times in one day.

Held here, on the compose files themselves, for every service that runs on live:

* a ceiling (``mem_limit`` or ``deploy.resources.limits.memory``) no larger than the VM;
* ``oom_score_adj`` below zero, so anything a gate starts (which has 0) is the kernel's choice first;
* the databases last of all (-900), and nothing live ahead of them;
* the floors (``mem_reservation``) together leave room for a gate.

``scripts/ops/live_memory.py`` makes the running containers agree with these files without restarting
them; this test is what keeps the files worth agreeing with.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

# the checkout's root — or "/", where the backend test lane mounts the compose files read-only
ROOT = next((p for p in (Path(__file__).resolve().parents[2], Path("/")) if (p / "docker-compose.webchat.yml").is_file()),
            Path(__file__).resolve().parents[2])
VM = 12 * 1024  # MB — %USERPROFILE%/.wslconfig
LIVE = {
    "docker-compose.yml": ["postgres", "redis", "backend", "gateway", "edge", "rate-limiter", "dlp-sidecar", "ner-service", "log-worker",
                           "mcp-gateway", "portal", "admin-ui", "ops-watch", "autoheal", "prometheus", "grafana", "redis-exporter",
                           "mock-provider"],
    "docker-compose.webchat.yml": ["postgres-webchat", "redis-webchat", "chat-edge-api", "web-chat", "kb-models", "backup"],
}
DATA = {"postgres", "postgres-webchat", "redis", "redis-webchat"}
REACHABLE = all((ROOT / f).is_file() for f in LIVE)


def _mb(v) -> int:
    if v in (None, ""):
        return 0
    s = str(v).strip().lower()
    if "${" in s:  # "${AM_RUNNER_MEMORY:-768m}": the default
        s = s.split(":-", 1)[1].rstrip("}") if ":-" in s else "0"
    for suffix, mult in (("gb", 1024), ("g", 1024), ("mb", 1), ("m", 1)):
        if s.endswith(suffix):
            return int(float(s[: -len(suffix)]) * mult)
    return int(float(s)) // (1 << 20)


def _services(live=None):
    for file, names in (live or LIVE).items():
        doc = yaml.safe_load((ROOT / file).read_text(encoding="utf-8"))["services"]
        for name in names:
            assert name in doc, f"{file}: the service {name} is gone — take it off this list or put it back"
            s = doc[name]
            res = ((s.get("deploy") or {}).get("resources") or {})
            yield file, name, {
                "ceiling": _mb(s.get("mem_limit") or (res.get("limits") or {}).get("memory")),
                "floor": _mb(s.get("mem_reservation") or (res.get("reservations") or {}).get("memory")),
                "turn": s.get("oom_score_adj"),
            }


pytestmark = pytest.mark.skipif(not REACHABLE, reason="the compose files are not reachable from this lane")


def test_every_live_service_has_a_ceiling_no_larger_than_the_vm():
    without = [f"{f}:{n}" for f, n, m in _services() if not 0 < m["ceiling"] <= VM]
    assert not without, f"no ceiling, or one larger than the Docker VM: {without}"


def test_every_live_service_has_its_turn_and_a_gate_goes_first():
    bad = [f"{f}:{n} ({m['turn']})" for f, n, m in _services() if not isinstance(m["turn"], int) or not -1000 < m["turn"] < 0]
    assert not bad, f"a live service whose turn is not below a gate's (0): {bad}"


def test_the_databases_are_the_last_to_go():
    turns = {n: m["turn"] for _, n, m in _services()}
    assert all(turns[d] == -900 for d in DATA), {d: turns[d] for d in DATA}
    ahead = [n for n, t in turns.items() if n not in DATA and t <= -900]
    assert not ahead, f"a service that would outlive a database: {ahead}"


def test_the_floors_leave_room_for_a_gate():
    floors = sum(m["floor"] for _, _, m in _services())
    assert 4 * 1024 <= floors <= 7 * 1024, f"the floors add up to {floors} MB of a {VM} MB VM"
    # … and no floor is above its own ceiling
    assert not [n for _, n, m in _services() if m["floor"] > m["ceiling"]]
