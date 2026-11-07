"""AM19b — a step of the gate passes on what is true, not on what it intended.

On 14 Oct 2026 ``gate.sh stack`` was given an 8-character sha. Compose tried to pull an image tag that
does not exist, the chat-edge stayed down, and the step said ok — the eighth time a step reported success
from its intention (fail-open in AM-1, templates in AM-8, the form filled out of sight in AM-10, one field
of six in AM-11, 3600 against 3,600.00 in AM-14, the smoke that did not care who answered in AM-15, probes
aimed at dead routes). The same day the swap of the web chat went unmeasured — its results folder did not
exist — and printed a verdict of zero requests as if it were one.

Held here with stand-ins for Docker, curl and sleep (nothing real is started):

* the test stack's ``up`` refuses, before Compose is asked anything, when an image it would run is not on
  the host — the missing image is named, with the hint that a tag is the 7-character sha;
* its ``check`` is red, naming the container, when one it started is not running or not healthy, or when
  the edge, the runner or the backend runs an image other than the one asked; green when all is as asked;
* the test clients' ``up`` is red when a client's door does not answer;
* the swap is not started when its measurement cannot be written (nothing live is touched); it is red,
  saying NOT MEASURED, when the poll recorded nothing; red when the service never answered 200 ten times
  in a row; green with the probe's figures otherwise;
* AM24c: a swap killed the hard way (no EXIT trap runs) leaves no poll behind asking the service. Until then the
  two cases that never answer waited the swap's whole 120 s window — with the stand-in sleep, 240 tries of process
  spawns, about two minutes on the laptop, at this file's own 120 s limit; over it Python killed the swap, its poll
  lived on holding the output pipe, and the host tests' lane waited on it until its 600 s ceiling. Those two now ask
  for a 5 s window (SWAP_WAIT_S), and the poll stops itself when the swap is gone.

    python -m unittest discover -s scripts/tests -p "test_am19b_steps_assert.py" -v
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash")

DOCKER = r'''#!/bin/sh
# a stand-in for Docker: records what it was asked; answers from the files the test wrote
echo "$@" >> "$FAKE_CALLS"
# AM24 — what the swap asks Compose (the services its files hold, the container a service runs, the swap itself)
# and Docker's events
case "$1" in
  compose)
    case " $* " in
      *" config --services "*)
        if [ -n "${FAKE_CONFIG_RC:-}" ]; then echo "no configuration file provided: not found" >&2; exit "$FAKE_CONFIG_RC"; fi
        printf '%s\n' ${FAKE_SERVICES:-chat-edge-api web-chat admin-ui agent-runner}; exit 0 ;;
      *" ps -q "*)
        n=$(cat "$FAKE_CALLS.ps" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$FAKE_CALLS.ps"
        if [ "$n" -eq 1 ]; then echo "${FAKE_BEFORE-0ld0ld0ld0ld0ld}"; else echo "${FAKE_AFTER-new0new0new0new}"; fi
        exit 0 ;;
      *" up "*)
        if [ -n "${FAKE_UP_RC:-}" ]; then echo "no such service: ${FAKE_UP_SAYS:-x}" >&2; exit "$FAKE_UP_RC"; fi
        exit 0 ;;
    esac ;;
  events) [ -f "${FAKE_EVENTS:-/nonexistent}" ] && cat "$FAKE_EVENTS"; exit 0 ;;
esac
case "$1 $2" in
  "image inspect")
    shift 2
    for img; do case " ${FAKE_MISSING:-} " in *" $img "*) echo "Error response from daemon: No such image: $img" >&2; exit 1 ;; esac; done
    exit 0 ;;
  "ps "*) [ -f "${FAKE_PS:-/nonexistent}" ] && cat "$FAKE_PS"; exit 0 ;;
  "inspect "*)
    eval "last=\${$#}"
    line=$(grep "^$last " "${FAKE_STATE:-/nonexistent}" 2>/dev/null | head -1)
    if [ -n "$line" ]; then echo "$line"; else echo healthy; fi
    exit 0 ;;
  *) exit 0 ;;
esac
'''
CURL = r'''#!/bin/sh
# (AM24: FAKE_DOWN=n — the door does not answer from its 7th ask, for n asks: a swap with a gap)
if [ -n "${FAKE_DOWN:-}" ]; then
  n=$(cat "$FAKE_CALLS.curl" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$FAKE_CALLS.curl"
  if [ "$n" -gt 6 ] && [ "$n" -le $((6 + FAKE_DOWN)) ]; then printf 000; exit 0; fi
fi
printf '%s' "${FAKE_CODE:-200}"
'''
# (AM24c: a test that may leave a loop behind ends it with FAKE_STOP — the next sleep the loop takes ends the loop)
SLEEP = '#!/bin/sh\nif [ -n "${FAKE_STOP:-}" ] && [ -f "$FAKE_STOP" ]; then kill "$PPID" 2>/dev/null; fi\nexit 0\n'
POWERSHELL = "#!/bin/sh\necho 'Online 1 100'\n"

# the stack as it stands when all is well: name status exit-code health image
STATE_OK = """sran-test-chat-edge-api-1 running 0 healthy ai-gateway-chat-edge-api:abc1234
sran-test-agent-runner running 0 healthy ai-gateway-agent-runner:abc1234
sran-test-backend-1 running 0 healthy ai-gateway-backend:2fa9bb8
sran-test-postgres-1 running 0 healthy postgres:16
sran-test-mail-sink-1 running 0 - axllent/mailpit:v1
sran-agent-market-test-web-fetcher-1 running 0 - ai-gateway-web-fetcher:abc1234
sran-agent-market-test-agent-market-init-1 exited 0 - alpine:3.20
"""


@unittest.skipIf(BASH is None or "system32" in (BASH or "").lower(), "no POSIX bash on PATH")
class StepsAssert(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="steps-assert-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        folder = self.tmp / "bin"
        folder.mkdir()
        for name, body in (("docker", DOCKER), ("curl", CURL), ("sleep", SLEEP), ("powershell", POWERSHELL)):
            (folder / name).write_text(body, encoding="utf-8", newline="\n")
            os.chmod(folder / name, 0o755)
        self.calls = self.tmp / "calls.txt"
        self.state = self.tmp / "state.txt"
        self.ps = self.tmp / "ps.txt"
        self.state.write_text(STATE_OK, encoding="utf-8", newline="\n")
        self.ps.write_text("".join(l.split(" ")[0] + "\n" for l in STATE_OK.splitlines()), encoding="utf-8", newline="\n")
        self.env = {**os.environ, "PATH": str(folder) + os.pathsep + os.environ.get("PATH", ""),
                    "FAKE_CALLS": self.calls.as_posix(), "FAKE_STATE": self.state.as_posix(), "FAKE_PS": self.ps.as_posix(),
                    "TS_BACKEND_TAG": "2fa9bb8", "AM_FETCHER_TAG": "abc1234"}
        self.env.pop("RUN_ON_BATTERY", None)
        found = subprocess.run([BASH, "-c", "command -v docker; command -v curl; command -v sleep"], env=self.env, capture_output=True, text=True)
        assert found.stdout.count(self.tmp.name) == 3, f"the stand-ins are not first on PATH: {found.stdout!r}"

    def run_it(self, *command: str, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run([BASH, *command], cwd=ROOT, env={**self.env, **env}, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120)

    def calls_made(self) -> str:
        return self.calls.read_text(encoding="utf-8") if self.calls.exists() else ""

    # ── the test stack ──────────────────────────────────────────────────────────────────────────────

    def test_up_refuses_before_compose_when_an_image_is_not_on_the_host(self):
        r = self.run_it("scripts/agent-market/test_stack.sh", "up", "abc1234d", FAKE_MISSING="ai-gateway-chat-edge-api:abc1234d ai-gateway-agent-runner:abc1234d")
        self.assertEqual(r.returncode, 6, r.stdout + r.stderr)
        self.assertIn("RED", r.stdout)
        self.assertIn("ai-gateway-chat-edge-api:abc1234d", r.stdout)
        self.assertIn("ai-gateway-agent-runner:abc1234d", r.stdout)
        self.assertIn("7-character", r.stdout)
        self.assertNotIn("compose", self.calls_made(), "Compose was asked to start a stack whose image does not exist")

    def test_check_is_green_when_every_container_runs_and_the_three_run_the_images_asked(self):
        r = self.run_it("scripts/agent-market/test_stack.sh", "check", "abc1234")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("stack check: ok", r.stdout)
        self.assertIn("7 containers", r.stdout)
        self.assertNotIn("RED", r.stdout)

    def test_check_is_red_naming_a_container_that_is_not_healthy(self):
        self.state.write_text(STATE_OK.replace("sran-test-chat-edge-api-1 running 0 healthy", "sran-test-chat-edge-api-1 running 0 unhealthy"), encoding="utf-8", newline="\n")
        r = self.run_it("scripts/agent-market/test_stack.sh", "check", "abc1234")
        self.assertEqual(r.returncode, 7, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"RED.*sran-test-chat-edge-api-1.*unhealthy")

    def test_check_is_red_naming_a_container_that_is_not_running(self):
        self.state.write_text(STATE_OK.replace("sran-test-agent-runner running 0 healthy", "sran-test-agent-runner exited 1 -"), encoding="utf-8", newline="\n")
        r = self.run_it("scripts/agent-market/test_stack.sh", "check", "abc1234")
        self.assertEqual(r.returncode, 7, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"RED.*sran-test-agent-runner.*exited")

    def test_check_is_red_when_the_edge_runs_another_image_than_the_one_asked(self):
        # (what the 8-character sha left behind: the edge still up on the previous card's image, healthy)
        self.state.write_text(STATE_OK.replace("ai-gateway-chat-edge-api:abc1234", "ai-gateway-chat-edge-api:5ad442c"), encoding="utf-8", newline="\n")
        r = self.run_it("scripts/agent-market/test_stack.sh", "check", "abc1234")
        self.assertEqual(r.returncode, 7, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"RED.*sran-test-chat-edge-api-1.*ai-gateway-chat-edge-api:5ad442c.*ai-gateway-chat-edge-api:abc1234")

    def test_check_is_red_when_a_container_the_stack_needs_is_missing(self):
        self.ps.write_text("".join(l + "\n" for l in self.ps.read_text(encoding="utf-8").splitlines() if "backend" not in l), encoding="utf-8", newline="\n")
        r = self.run_it("scripts/agent-market/test_stack.sh", "check", "abc1234")
        self.assertEqual(r.returncode, 7, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"RED.*sran-test-backend-1.*not there")

    def test_the_clients_up_is_red_when_a_door_does_not_answer(self):
        # (the tool resolves its argument as a git revision, so the tip stands in for a built one)
        r = self.run_it("scripts/agent-market/test_clients.sh", "up", "HEAD", FAKE_CODE="000")
        self.assertEqual(r.returncode, 7, r.stdout + r.stderr)
        self.assertIn("RED", r.stdout)
        self.assertIn("13002", r.stdout)
        self.assertIn("13000", r.stdout)
        r = self.run_it("scripts/agent-market/test_clients.sh", "up", "HEAD", FAKE_CODE="200")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("RED", r.stdout)

    # ── the swap ────────────────────────────────────────────────────────────────────────────────────

    def test_a_swap_whose_measurement_cannot_be_written_is_not_started(self):
        blocker = self.tmp / "a-file"
        blocker.write_text("not a folder\n", encoding="utf-8")
        r = self.run_it("scripts/ops/swap.sh", "chat-edge-api", "https://localhost:4000/health", "-p", "nothing",
                        SWAP_OUT=(blocker / "results" / "swap.txt").as_posix())
        self.assertEqual(r.returncode, 5, r.stdout + r.stderr)
        self.assertIn("not started", r.stdout)
        self.assertIn("cannot be written", r.stdout)
        self.assertNotIn("compose", self.calls_made(), "a swap that could not be measured touched live")

    def test_a_swap_the_poll_did_not_record_says_not_measured_and_is_red(self):
        # (/dev/null takes every line and keeps none: the swap ends with nothing on record)
        r = self.run_it("scripts/ops/swap.sh", "chat-edge-api", "https://localhost:4000/health", "-p", "nothing", SWAP_OUT="/dev/null",
                        SWAP_WAIT_S="5")
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("NOT MEASURED", r.stdout)
        self.assertNotIn("probe: 0 requests", r.stdout)

    def test_a_swap_after_which_the_service_never_answers_is_red(self):
        r = self.run_it("scripts/ops/swap.sh", "chat-edge-api", "https://localhost:4000/health", "-p", "nothing",
                        SWAP_OUT=(self.tmp / "swap.txt").as_posix(), FAKE_CODE="503", SWAP_WAIT_S="5")
        self.assertEqual(r.returncode, 9, r.stdout + r.stderr)
        self.assertIn("RED", r.stdout)
        self.assertIn("did not answer 200 ten times in a row within 5 s of the swap", r.stdout)
        self.assertIn("compose -p nothing up -d --no-deps chat-edge-api", self.calls_made())

    def test_a_swap_killed_without_its_trap_leaves_no_poll_asking_the_service(self):
        out = self.tmp / "swap.txt"
        stop = self.tmp / "stop"
        lines = lambda: len(out.read_text(encoding="utf-8").splitlines()) if out.exists() else 0

        def stop_a_loop_left_behind():  # (red: the loop ends at its next sleep — waited for before the folder goes)
            stop.write_text("stop\n", encoding="utf-8")
            for _ in range(40):
                n = lines()
                time.sleep(0.5)
                if lines() == n:
                    return
        self.addCleanup(stop_a_loop_left_behind)
        p = subprocess.Popen([BASH, "scripts/ops/swap.sh", "chat-edge-api", "https://localhost:4000/health", "-p", "nothing"], cwd=ROOT,
                             env={**self.env, "SWAP_OUT": out.as_posix(), "FAKE_CODE": "503", "FAKE_STOP": stop.as_posix()},
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 90
        while lines() < 5 and time.time() < deadline:
            time.sleep(0.2)
        self.assertGreaterEqual(lines(), 5, "the swap's poll never started")
        p.kill()  # the hard way: no EXIT trap runs
        p.wait(timeout=30)
        time.sleep(5)  # a poll still on its way round finishes this turn and looks again
        n = lines()
        time.sleep(5)
        self.assertEqual(lines(), n, "the swap was killed and its poll went on asking the service")

    def test_a_measured_swap_after_which_the_service_answers_is_green_with_its_figures(self):
        r = self.run_it("scripts/ops/swap.sh", "chat-edge-api", "https://localhost:4000/health", "-p", "nothing",
                        SWAP_OUT=(self.tmp / "swap.txt").as_posix())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"probe: [1-9][0-9]* requests, 0 not 200")
        self.assertNotIn("RED", r.stdout)
        self.assertNotIn("NOT MEASURED", r.stdout)


if __name__ == "__main__":
    unittest.main()
