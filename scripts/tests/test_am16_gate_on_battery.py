"""AM16 — on battery the gate starts nothing, and neither does a swap, a build or the test stack.

On 12 Oct 2026 the power was printed in front of ``gate.sh lanes backend_lane``; it said "battery" and
the lane ran, 510 s against 321 s on mains. The owner: a warning that does not stop anything is a
warning nobody reads — block it outright, with one explicit step to override. ``scripts/ops/power.sh``
is the check (held by test_power_on_battery.py); this holds that the gate and the tools beside it ask
it, without Docker and whatever the machine running the test is plugged into:

* a heavy phase on battery is REFUSED, exit 3: its command does not run and the lock is not taken;
* with ``RUN_ON_BATTERY=yes`` on the command it goes on, and says on its own output that it did;
* every heavy phase asks before anything else, and a run of lanes asks again before each lane (the
  charger can be pulled an hour in); ``preflight`` and ``stack-stop`` start nothing and are not asked;
* the swap of a live service, the three image builds, the engine's lane, the test stack's start and
  its clients' build refuse the same way — Docker is not called once.

    python -m unittest discover -s scripts/tests -p "test_am16_gate_on_battery.py" -v
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATE = (ROOT / "scripts" / "gate.sh").read_text(encoding="utf-8")
BASH = shutil.which("bash")

MAINS, BATTERY = "Online 1 100", "Offline 1 63"
HEAVY = ("lanes", "it", "build", "stack", "walk", "smoke", "drill", "backup")


def arm(name: str) -> str:
    m = re.search(rf"(?ms)^  {name}\)\n(.*?)^    ;;\n", GATE)
    assert m, f"scripts/gate.sh has no {name} phase"
    return m.group(1)


class EveryHeavyPhaseAsks(unittest.TestCase):
    def test_the_lock_asks_the_power_before_it_is_taken(self):
        body = re.search(r"(?ms)^lock\(\) \{\n(.*?)^\}", GATE).group(1)
        self.assertLess(body.index('power "gate $1"'), body.index("mkdir"), "the lock is taken before the power is asked")
        power = re.search(r"(?ms)^power\(\) \{.*?^\}", GATE).group(0)
        self.assertIn("scripts/ops/power.sh need", power)
        self.assertIn("exit 3", power)

    def test_every_heavy_phase_takes_the_lock_before_it_starts_anything(self):
        for name in HEAVY:
            # (what the phase runs, in order: the helpers it only defines are not steps of it)
            body = re.sub(r"(?m)^\s+\w+\(\) \{.*\}\s*$\n", "", arm(name))
            body = re.sub(r"(?ms)^\s+\w+\(\) \{.*?^\s+\}\n", "", body)
            body = "\n".join(l for l in body.splitlines() if not l.lstrip().startswith("#"))
            self.assertRegex(body, r"(?m)^\s+lock ", f"{name} takes no lock: nothing asks the power for it")
            at = re.search(r"(?m)^\s+lock ", body).start()
            for first in ("stack_stop", "room", "guard", "one ", "docker ", "timeout "):
                if first in body:
                    self.assertLess(at, body.index(first), f"{name} reaches {first!r} before the lock")

    def test_a_run_of_lanes_asks_again_before_each_lane(self):
        room = re.search(r"(?ms)^room\(\) \{.*?^\}", GATE).group(0)
        self.assertIn("power ", room, "the charger pulled between two lanes stops nothing")
        self.assertRegex(arm("lanes"), r"ran\+=\(\"\$1\"\); room; one ", "a lane no longer waits for room (where the power is asked)")

    def test_what_starts_nothing_is_not_asked(self):
        self.assertNotRegex(arm("preflight"), r"(?m)^\s+(lock|power|room)\b")
        self.assertRegex(GATE, r"(?m)^  stack-stop\) stack_stop ;;$")


@unittest.skipIf(BASH is None or "system32" in (BASH or "").lower(), "no POSIX bash on PATH")
class OnBattery(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gate-power-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.marker = self.tmp / "ran"

    def run_it(self, power: str, *command: str, **env: str) -> subprocess.CompletedProcess:
        folder = self.tmp / "bin"
        folder.mkdir(exist_ok=True)
        calls = (self.tmp / "docker_calls.txt").as_posix()
        (folder / "powershell").write_text(f"#!/bin/sh\necho '{power}'\n", encoding="utf-8", newline="\n")
        # (the stand-in for Docker records what it was asked and answers "healthy": a swap that was wrongly
        # let through then ends in seconds, having touched nothing real)
        (folder / "docker").write_text(f"#!/bin/sh\necho \"$@\" >> '{calls}'\necho healthy\nexit 0\n", encoding="utf-8", newline="\n")
        for name in ("powershell", "docker"):
            os.chmod(folder / name, 0o755)
        e = {**os.environ, "PATH": str(folder) + os.pathsep + os.environ.get("PATH", ""),
             "GATE_LOCK": (self.tmp / "lock").as_posix(), "GATE_RESULTS": (self.tmp / "results").as_posix(), "GATE_WAIT_S": "0",
             "SWAP_OUT": (self.tmp / "swap.txt").as_posix()}
        e.pop("RUN_ON_BATTERY", None)
        e.update(env)
        # (the stand-ins must be the ones found, or a test of a refusal would start the real thing)
        found = subprocess.run([BASH, "-c", "command -v powershell; command -v docker"], env=e, capture_output=True, text=True)
        assert found.stdout.count("/bin/") >= 2 and self.tmp.name in found.stdout, f"the stand-ins are not first on PATH: {found.stdout!r}"
        return subprocess.run([BASH, *command], cwd=ROOT, env=e, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)

    def docker_calls(self) -> str:
        """What Docker was asked to do. Reading a label (`inspect`) starts nothing and is not counted."""
        f = self.tmp / "docker_calls.txt"
        lines = f.read_text(encoding="utf-8").splitlines() if f.exists() else []
        return "\n".join(l for l in lines if not l.startswith("inspect "))

    def test_a_phase_on_battery_is_refused_and_its_command_does_not_run(self):
        r = self.run_it(BATTERY, "scripts/gate.sh", "walk", "--", "touch", self.marker.as_posix(), GATE_FREE_MB="99999")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("REFUSED", r.stdout)
        self.assertIn("on battery (63%)", r.stdout)
        self.assertIn("RUN_ON_BATTERY=yes", r.stdout)
        self.assertFalse(self.marker.exists(), "the refused phase ran its command")
        self.assertFalse((self.tmp / "lock").exists(), "a refused phase took the lock")
        self.assertEqual(self.docker_calls(), "", "a refused phase called Docker")

    def test_a_named_lane_on_battery_is_refused_before_the_test_stack_is_stopped(self):
        r = self.run_it(BATTERY, "scripts/gate.sh", "lanes", "smoke_units", GATE_FREE_MB="99999")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("gate lanes was not started", r.stdout)
        self.assertEqual(self.docker_calls(), "")
        self.assertEqual([p.name for p in (self.tmp / "results").glob("*")], [], "a refused lane wrote a result")

    def test_the_override_on_the_command_lets_it_on_and_is_said(self):
        # (it is let past the power to the next check — a host with no room — so nothing real is started)
        r = self.run_it(BATTERY, "scripts/gate.sh", "walk", "--", "touch", self.marker.as_posix(), GATE_FREE_MB="900", RUN_ON_BATTERY="yes")
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("ON BATTERY, RUN ANYWAY", r.stdout)
        self.assertIn("900 MB free", r.stdout)

    def test_on_mains_nothing_is_said_about_the_power(self):
        r = self.run_it(MAINS, "scripts/gate.sh", "walk", "--", "touch", self.marker.as_posix(), GATE_FREE_MB="900")
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertNotRegex(r.stdout, r"(?i)battery")

    def test_the_swap_of_a_live_service_on_battery_touches_nothing(self):
        r = self.run_it(BATTERY, "scripts/ops/swap.sh", "chat-edge-api", "health:nothing", "-p", "nothing")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("the swap of chat-edge-api was not started", r.stdout)
        self.assertEqual(self.docker_calls(), "", "a refused swap called Docker")
        self.assertFalse((self.tmp / "swap.txt").exists(), "a refused swap began its measurement")

    def test_the_builds_the_lane_and_the_test_stack_refuse_the_same_way(self):
        for command in (("scripts/agent-market/build.sh", "HEAD"), ("scripts/agent-market/build-fetcher.sh", "HEAD"),
                        ("scripts/agent-market/build-converter.sh", "HEAD"), ("scripts/agent-market/python-lane.sh", "-k", "nothing"),
                        ("scripts/agent-market/test_stack.sh", "up", "nothing"), ("scripts/agent-market/test_clients.sh", "build", "HEAD")):
            r = self.run_it(BATTERY, *command)
            self.assertEqual(r.returncode, 3, f"{command}: {r.stdout}{r.stderr}")
            self.assertIn("REFUSED", r.stdout, command)
            self.assertEqual(self.docker_calls(), "", f"{command[0]} called Docker on battery")

    def test_the_swap_asks_the_runner_through_its_health_check(self):
        swap = (ROOT / "scripts" / "ops" / "swap.sh").read_text(encoding="utf-8")
        self.assertIn("health:*)", swap)
        self.assertLess(swap.index("power.sh"), swap.index(': > "$OUT"'), "the swap empties its result file before it asks")


if __name__ == "__main__":
    unittest.main()
