"""AM24 §3.2 and Part 2 — a swap that swapped nothing is not a pass, and a slow one says so.

On 16 Oct 2026 the admin's swap was run against the web chat's compose file: Compose said "no such service",
nothing was recreated, the old admin went on answering 200, and the swap printed a pass (occurrence 9). With the
stand-ins of test_am19b_steps_assert.py for Docker, curl and sleep (nothing real is started):

* a service the compose files given do not hold is red before anything is touched, naming the service and the
  files — the fixture is the very run of 16 Oct: the admin, against the web chat's file;
* Compose unable to read its files, or refusing the swap, is red; a service that still runs the container it
  ran before is red (nothing was swapped);
* a swap over its ceiling says SLOW, with where the time went from Docker's events, and still exits 0 — a slow
  swap is not a failed one; every measured swap adds its line to the series.

    python -m unittest discover -s scripts/tests -p "test_am24_swap.py" -v
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location("am19b_steps", Path(__file__).with_name("test_am19b_steps_assert.py"))
am19b = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(am19b)

ADMIN = ("scripts/ops/swap.sh", "admin-ui", "https://localhost:3000/healthz", "-p", "ai-gateway", "-f", "docker-compose.webchat.yml")


class Swap(am19b.StepsAssert):
    def swap(self, *command: str, **env: str):
        env.setdefault("SWAP_OUT", (self.tmp / "swap.txt").as_posix())
        return self.run_it(*command, **env)

    def test_a_service_the_compose_files_do_not_hold_is_red_naming_both_and_nothing_is_touched(self):
        # the run of 16 Oct: the admin's swap, against the web chat's compose file (which holds no admin-ui)
        r = self.swap(*ADMIN, FAKE_SERVICES="web-chat postgres-webchat")
        self.assertEqual(r.returncode, 6, r.stdout + r.stderr)
        self.assertIn("RED", r.stdout)
        self.assertIn("no service named admin-ui", r.stdout)
        self.assertIn("docker-compose.webchat.yml", r.stdout)
        self.assertNotIn(" up ", self.calls_made(), "a swap of a service its files do not hold asked Compose to swap")
        self.assertNotIn("probe:", r.stdout)

    def test_compose_unable_to_read_its_files_is_red(self):
        r = self.swap(*ADMIN, FAKE_CONFIG_RC="14")
        self.assertEqual(r.returncode, 6, r.stdout + r.stderr)
        self.assertIn("cannot read the files given", r.stdout)

    def test_compose_refusing_the_swap_is_red(self):
        r = self.swap(*ADMIN, FAKE_SERVICES="admin-ui", FAKE_UP_RC="1", FAKE_UP_SAYS="admin-ui")
        self.assertEqual(r.returncode, 7, r.stdout + r.stderr)
        self.assertIn("Compose refused the swap of admin-ui", r.stdout)

    def test_a_swap_after_which_the_service_runs_the_same_container_is_red(self):
        r = self.swap(*ADMIN, FAKE_SERVICES="admin-ui", FAKE_BEFORE="abcdefabcdef0001", FAKE_AFTER="abcdefabcdef0001")
        self.assertEqual(r.returncode, 10, r.stdout + r.stderr)
        self.assertIn("nothing was swapped", r.stdout)
        self.assertNotIn("swap admin-ui done", r.stdout)

    def test_a_swap_over_its_ceiling_says_slow_with_where_the_time_went_and_is_not_a_failure(self):
        events = self.tmp / "events.txt"
        old, new = "0ld0ld0ld0ld0ld", "new0new0new0new"
        t = 1_760_720_673_000_000_000
        events.write_text("\n".join([
            f"{t} create {new}", f"{t + 100_000_000} kill {old}", f"{t + 11_400_000_000} die {old}",
            f"{t + 11_500_000_000} start {new}", f"{t + 12_600_000_000} health_status: healthy {new}"]) + "\n",
            encoding="utf-8", newline="\n")
        series = self.tmp / "series.tsv"
        r = self.swap("scripts/ops/swap.sh", "chat-edge-api", "https://localhost:4000/health", "-p", "nothing",
                      FAKE_DOWN="4", SWAP_CEILING_S="0.4", FAKE_EVENTS=events.as_posix(), SWAP_SERIES=series.as_posix())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("SLOW SWAP: chat-edge-api", r.stdout)
        self.assertIn("over its ceiling of 0.4 s", r.stdout)
        self.assertIn("the old container stopped in 11.3 s", r.stdout)
        self.assertIn("the new one started 11.5 s after it was made", r.stdout)
        self.assertIn("healthy 1.1 s after its start", r.stdout)
        self.assertRegex(r.stdout.splitlines()[-1], r"swap chat-edge-api done .* — SLOW")
        line = series.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(line), 1)
        f = line[0].split("\t")
        self.assertEqual((f[1], f[2], f[3], f[7], f[8], f[9], f[10], f[11]),
                         ("chat-edge-api", old[:12], new[:12], "0.4", "11.3", "11.5", "1.1", "slow"))

    def test_every_measured_swap_adds_its_line_to_the_series(self):
        series = self.tmp / "series.tsv"
        for _ in range(2):
            (self.tmp / "calls.txt.ps").unlink(missing_ok=True)
            r = self.swap("scripts/ops/swap.sh", "chat-edge-api", "https://localhost:4000/health", "-p", "nothing",
                          SWAP_SERIES=series.as_posix())
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("SLOW", r.stdout)
            self.assertIn("(ceiling 5 s)", r.stdout)
        rows = [l.split("\t") for l in series.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(x[1], x[7], x[11]) for x in rows], [("chat-edge-api", "5", "ok")] * 2)

    def test_a_health_check_swap_has_the_health_checks_ceiling(self):
        r = self.swap("scripts/ops/swap.sh", "agent-runner", "health:sran-agent-runner", "-p", "nothing")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("(ceiling 20 s)", r.stdout)


# the harness's own tests run in their own module, not again here
for _name in [n for n in dir(am19b.StepsAssert) if n.startswith("test_")]:
    setattr(Swap, _name, None)


if __name__ == "__main__":
    unittest.main()
