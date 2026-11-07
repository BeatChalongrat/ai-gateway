"""Nothing heavy starts on battery — and the refusal is in the tool, not in a line to read.

The host is a laptop that also runs live. On 12 Oct 2026 the power was printed before the backend lane;
it said "battery", and the lane ran anyway, at half speed. A warning that stops nothing is a warning
nobody reads. ``scripts/ops/power.sh`` is now asked by the tools themselves. Held here, without Docker
and whatever the machine running the test is plugged into (what Windows would say is given by a
stand-in ``powershell`` put first on PATH; the tools have no setting that says "this is mains"):

* on battery, and when the power cannot be read, the answer is REFUSED and exit 3, naming what did not
  start and the one way to run it anyway;
* the override is the word ``yes`` on the command itself — any other word is still refused — and the
  command says that it is running on battery because it was told to;
* on mains, and on a machine with no battery, nothing is said and nothing is refused;
* the backend deploy and the image build ask before they touch anything: on battery Docker is not
  called once. A dry run of the deploy is not refused.

    python -m unittest discover -s scripts/tests -p "test_power_on_battery.py" -v
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash")  # (by PATH: on Windows a bare "bash" can be the WSL launcher)

MAINS, BATTERY, NO_BATTERY, UNREADABLE = "Online 1 100", "Offline 1 63", "Online 0 255", ""


def stand_ins(tmp: Path, power: str) -> dict[str, str]:
    """A PATH whose first folder answers for Windows' power and records every call to Docker."""
    folder = tmp / "bin"
    folder.mkdir(exist_ok=True)
    calls = (tmp / "docker_calls.txt").as_posix()
    (folder / "powershell").write_text(f"#!/bin/sh\necho '{power}'\n", encoding="utf-8", newline="\n")
    (folder / "docker").write_text(f"#!/bin/sh\necho \"$@\" >> '{calls}'\nexit 0\n", encoding="utf-8", newline="\n")
    for name in ("powershell", "docker"):
        os.chmod(folder / name, 0o755)
    return {**os.environ, "PATH": str(folder) + os.pathsep + os.environ.get("PATH", "")}


@unittest.skipIf(BASH is None or "system32" in (BASH or "").lower(), "no POSIX bash on PATH")
class PowerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="power-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def run_it(self, power: str, *command: str, **env: str) -> subprocess.CompletedProcess:
        e = stand_ins(self.tmp, power)
        e.pop("RUN_ON_BATTERY", None)  # (the shell that runs the tests may have been given it)
        e.update(env)
        # (the stand-ins must be the ones found, or a test of a refusal would start the real thing)
        found = subprocess.run([BASH, "-c", "command -v powershell; command -v docker"], env=e, capture_output=True, text=True)
        assert found.stdout.count("/bin/") >= 2 and self.tmp.name in found.stdout, f"the stand-ins are not first on PATH: {found.stdout!r}"
        return subprocess.run([BASH, *command], cwd=ROOT, env=e, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)

    def docker_calls(self) -> str:
        f = self.tmp / "docker_calls.txt"
        return f.read_text(encoding="utf-8") if f.exists() else ""


class TheAnswer(PowerTest):
    def test_it_says_what_the_power_is(self):
        for power, word in ((MAINS, "mains"), (BATTERY, "battery 63%"), (NO_BATTERY, "no battery"), (UNREADABLE, "unknown"), ("Unknown 1 50", "unknown")):
            r = self.run_it(power, "scripts/ops/power.sh")
            self.assertEqual((r.returncode, r.stdout.strip()), (0, word), power)

    def test_on_battery_it_refuses_naming_what_did_not_start_and_the_one_way_to_run_it_anyway(self):
        r = self.run_it(BATTERY, "scripts/ops/power.sh", "need", "the long lane")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("REFUSED", r.stdout)
        self.assertIn("on battery (63%)", r.stdout)
        self.assertIn("the long lane was not started", r.stdout)
        self.assertIn("RUN_ON_BATTERY=yes", r.stdout)

    def test_power_that_cannot_be_read_is_refused_too(self):
        for power in (UNREADABLE, "Unknown 1 50", "nonsense"):
            r = self.run_it(power, "scripts/ops/power.sh", "need", "the long lane")
            self.assertEqual(r.returncode, 3, power)
            self.assertIn("could not be read", r.stdout)

    def test_on_mains_and_with_no_battery_nothing_is_said_and_nothing_is_refused(self):
        for power in (MAINS, NO_BATTERY):
            r = self.run_it(power, "scripts/ops/power.sh", "need", "the long lane")
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""), power)

    def test_the_override_is_the_word_yes_on_the_command_and_it_is_said(self):
        r = self.run_it(BATTERY, "scripts/ops/power.sh", "need", "the long lane", RUN_ON_BATTERY="yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ON BATTERY, RUN ANYWAY", r.stdout)
        self.assertIn("the long lane starts because RUN_ON_BATTERY=yes was given", r.stdout)
        for word in ("1", "true", "YES", "y", ""):
            r = self.run_it(BATTERY, "scripts/ops/power.sh", "need", "the long lane", RUN_ON_BATTERY=word)
            self.assertEqual(r.returncode, 3, f"RUN_ON_BATTERY={word!r} ran it")

    def test_no_setting_says_the_power_for_it_and_no_file_holds_the_override(self):
        src = (ROOT / "scripts" / "ops" / "power.sh").read_text(encoding="utf-8")
        code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
        names = set(__import__("re").findall(r"\$\{?([A-Z][A-Z0-9_]+)", code))
        self.assertEqual(names - {"BASH_SOURCE"}, {"RUN_ON_BATTERY"}, "the script reads a setting other than the override")
        self.assertNotRegex(code, r"\bsource\b|^\s*\. |\.env", "the override can come from a file")


class TheToolsAskFirst(PowerTest):
    def test_the_backend_deploy_on_battery_touches_nothing(self):
        r = self.run_it(BATTERY, "scripts/deploy-backend.sh", "--image", "ai-gateway-backend:nothing")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("REFUSED", r.stdout)
        self.assertIn("the backend deploy was not started", r.stdout)
        self.assertEqual(self.docker_calls(), "", "a refused deploy called Docker")

    def test_the_backend_deploy_on_mains_goes_on(self):
        r = self.run_it(MAINS, "scripts/deploy-backend.sh", "--image", "ai-gateway-backend:nothing")
        self.assertNotIn("REFUSED", r.stdout + r.stderr)
        self.assertIn("config", self.docker_calls(), "on mains the deploy did not reach its first step")

    def test_a_dry_run_of_the_deploy_is_not_refused(self):
        r = self.run_it(BATTERY, "scripts/deploy-backend.sh", "--image", "ai-gateway-backend:nothing", "--dry-run")
        self.assertNotIn("REFUSED", r.stdout + r.stderr)

    def test_the_image_build_on_battery_builds_nothing(self):
        r = self.run_it(BATTERY, "scripts/build-images.sh", "--rev", "HEAD", "admin-ui")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("the image build (admin-ui) was not started", r.stdout)
        self.assertEqual(self.docker_calls(), "", "a refused build called Docker")

    def test_each_asks_before_its_first_heavy_step(self):
        build = (ROOT / "scripts" / "build-images.sh").read_text(encoding="utf-8")
        self.assertLess(build.index('power.sh" need'), build.index("TMP=$(mktemp -d)"), "the build makes its folder before it asks")
        deploy = (ROOT / "scripts" / "deploy-backend.sh").read_text(encoding="utf-8")
        self.assertLess(deploy.index("power.sh need"), deploy.index('CONFIG_JSON=$("${DC[@]}" config'), "the deploy calls Docker before it asks")


if __name__ == "__main__":
    unittest.main()
