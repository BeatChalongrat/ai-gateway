"""AM24b Part 2 — the way back is kept by the command that leaves it, and the runbook cannot go stale unseen.

Every guard here has a test that makes its defect on purpose and shows the guard red (CARD-RULES §6d). With a
stand-in for Docker that keeps a small world — images by tag and id, containers, what each compose project runs — and
moves a tag when asked (nothing real is started or tagged):

* swap.sh --image keeps the running image as <repository>:rollback-<its revision>, points the tag the compose files
  run at the image given and records the swap; it refuses, before touching anything, an image not on the host, a
  compose tag that names one image for good, and — for a project that is not live — a tag another project runs (a
  live swap is told, never held up); a service with neither a door nor a health check is asked whether it runs;
* deploy-backend.sh moves the tag its own compose files run, never `latest` blindly: until AM24b a deploy to the test
  stack moved the tag live's backend and ops-watch start from. It refuses a revision's own tag, and — off live — a tag
  live runs; --rev refuses an image whose label says another commit; every deploy is recorded;
* scripts/ops/rollback_book.py check, against the committed runbook and a stand-in live built from its own state, is
  green — and red when a compose service has no line, when the page was edited by hand, when a tag it names is gone,
  when live runs something else, and when a deploy recorded since says something else; the gate's ledger counts its
  checks.

    python -m unittest discover -s scripts/tests -p "test_am24b_way_back.py" -v
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash")

_spec = importlib.util.spec_from_file_location("rollback_book", ROOT / "scripts" / "ops" / "rollback_book.py")
book = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(book)
_spec = importlib.util.spec_from_file_location("ledger", ROOT / "scripts" / "gate" / "ledger.py")
ledger = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ledger)

# The stand-in for Docker. $FAKE_WORLD (JSON) is the world it answers from; `tag` writes it back.
FAKE = r'''
import json, os, sys
W, C = os.environ["FAKE_WORLD"], os.environ["FAKE_CALLS"]
a = sys.argv[1:]
with open(C, "a", encoding="utf-8") as f:
    f.write(" ".join(a) + "\n")
w = json.load(open(W, encoding="utf-8"))
def save():
    json.dump(w, open(W, "w", encoding="utf-8"))
def img(ref):
    if ref in w["images"]:
        return w["images"][ref]
    return next((v for v in w["images"].values() if v["id"] == ref), None)
def fmt():
    for k in ("-f", "--format"):
        if k in a:
            return a[a.index(k) + 1]
    return ""
def out(s):
    sys.stdout.write(s + "\n")
    sys.exit(0)
if a[:1] == ["compose"]:
    if "config" in a:
        if "--services" in a:
            out("\n".join(w["services"]))
        if "--format" in a:
            out(json.dumps({"services": {s: ({"image": w["compose_image"][s]} if s in w.get("compose_image", {}) else {})
                                         for s in w["services"]}}))
        sys.exit(0)
    if "ps" in a and "-q" in a:
        n = w.get("ps_n", 0); w["ps_n"] = n + 1; save()
        q = w.get("ps_q") or [""]
        out(q[min(n, len(q) - 1)])
    if "up" in a:
        sys.exit(w.get("up_rc", 0))
    sys.exit(0)
cmd = a[0] if a else ""
if cmd == "version":
    out("27.0")
if cmd == "tag":
    i = img(a[1])
    if i is None:
        sys.stderr.write("Error: No such image: " + a[1] + "\n"); sys.exit(1)
    w["images"][a[2]] = dict(i); save(); sys.exit(0)
if cmd == "image" and a[1] == "inspect":
    f = fmt()
    lines = []
    for r in [x for x in a[2:] if x not in ("-f", "--format", f)]:
        i = img(r)
        if i is None:
            sys.stderr.write("Error: No such image: " + r + "\n"); sys.exit(1)
        lines.append(i.get("rev", "") if "revision" in f else i["id"] if "Id" in f else "{}")
    out("\n".join(lines))
if cmd == "inspect":
    f, name = fmt(), a[-1]
    c = w.get("containers", {}).get(name)
    if c is None:
        sys.exit(1)
    if "Health" in f and ".Name" in f:
        out("/" + name + " " + c.get("health", "healthy"))
    if "Health" in f:
        out(c.get("health", "healthy"))
    if ".State.Status" in f:
        out(c.get("status", "running"))
    if "revision" in f:
        out(c.get("rev", ""))
    if ".Image" in f:
        out(c["image"])
    out("")
if cmd == "ps":
    out("\n".join(w.get("ps_a", [])))
if cmd in ("run", "exec"):
    out("aaaa1111 (head)")
sys.exit(0)
'''
CURL = "#!/bin/sh\nprintf 200\n"
SLEEP = "#!/bin/sh\nexit 0\n"
POWERSHELL = "#!/bin/sh\necho 'Online 1 100'\n"
OLD, NEW = "sha256:" + "0" * 60 + "01d0", "sha256:" + "0" * 60 + "0e30"


@unittest.skipIf(BASH is None or "system32" in (BASH or "").lower(), "no POSIX bash on PATH")
class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="am24b-way-back-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        b = self.tmp / "bin"
        b.mkdir()
        (self.tmp / "fake_docker.py").write_text(FAKE, encoding="utf-8")
        docker = f'#!/bin/sh\nexec "{Path(sys.executable).as_posix()}" "{(self.tmp / "fake_docker.py").as_posix()}" "$@"\n'
        for name, body in (("docker", docker), ("curl", CURL), ("sleep", SLEEP), ("powershell", POWERSHELL)):
            (b / name).write_text(body, encoding="utf-8", newline="\n")
            os.chmod(b / name, 0o755)
        self.world_file, self.calls = self.tmp / "world.json", self.tmp / "calls.txt"
        self.record = self.tmp / "deploys.tsv"
        self.env = {**os.environ, "PATH": str(b) + os.pathsep + os.environ.get("PATH", ""), "MSYS_NO_PATHCONV": "1",
                    "FAKE_WORLD": self.world_file.as_posix(), "FAKE_CALLS": self.calls.as_posix(),
                    "DEPLOY_RECORD": self.record.as_posix(), "SWAP_OUT": (self.tmp / "swap.txt").as_posix(),
                    "SWAP_SERIES": (self.tmp / "series.tsv").as_posix()}
        for k in ("RUN_ON_BATTERY", "LIVE_PROJECTS", "TS_BACKEND_TAG"):
            self.env.pop(k, None)

    def world(self, **w):
        w.setdefault("images", {})
        self.world_file.write_text(json.dumps(w), encoding="utf-8")

    def now(self) -> dict:
        return json.loads(self.world_file.read_text(encoding="utf-8"))

    def run_it(self, *command: str, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run([BASH, *command], cwd=ROOT, env={**self.env, **env}, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=240)

    def tags_moved(self) -> list[str]:
        return [l for l in (self.calls.read_text(encoding="utf-8") if self.calls.exists() else "").splitlines()
                if l.startswith("tag ")]


class SwapKeepsTheWayBack(Harness):
    SVC = ("scripts/ops/swap.sh", "admin-ui", "http://127.0.0.1:23000/login")
    LIVE = ("-p", "ai-gateway", "-f", "docker-compose.yml")

    def admin_world(self, **extra):
        w = dict(services=["admin-ui", "portal"], ps_q=["c0ld", "cn3w"],
                 images={"ai-gateway-admin-ui:latest": {"id": OLD, "rev": "f01f1ee"},
                         "ai-gateway-admin-ui:134b023": {"id": NEW, "rev": "134b023"}},
                 containers={"c0ld": {"image": OLD, "rev": "f01f1ee"}, "cn3w": {"image": NEW, "rev": "134b023"}})
        w.update(extra)
        self.world(**w)

    def test_the_swap_keeps_the_running_image_points_the_compose_tag_and_records_it(self):
        # (until AM24b --image did not exist: it went to Compose as a file argument, and no tag was moved or kept)
        self.admin_world()
        r = self.run_it(*self.SVC, "--image", "ai-gateway-admin-ui:134b023", *self.LIVE)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.tags_moved(), [f"tag {OLD} ai-gateway-admin-ui:rollback-f01f1ee",
                                             "tag ai-gateway-admin-ui:134b023 ai-gateway-admin-ui:latest"])
        w = self.now()
        self.assertEqual(w["images"]["ai-gateway-admin-ui:rollback-f01f1ee"]["id"], OLD, "the way back was not kept")
        self.assertEqual(w["images"]["ai-gateway-admin-ui:latest"]["id"], NEW)
        self.assertIn("back with: bash scripts/ops/swap.sh admin-ui http://127.0.0.1:23000/login --image "
                      "ai-gateway-admin-ui:rollback-f01f1ee -p ai-gateway", r.stdout)
        f = self.record.read_text(encoding="utf-8").splitlines()[-1].split("\t")
        self.assertEqual(f[1:9], ["swap", "ai-gateway", "admin-ui", "ai-gateway-admin-ui:134b023", NEW, "134b023",
                                  "ai-gateway-admin-ui:rollback-f01f1ee", "ok"])
        self.assertIn("python scripts/ops/rollback_book.py write", r.stdout)

    def test_an_image_not_on_the_host_is_refused_before_anything_is_touched(self):
        self.admin_world()
        r = self.run_it(*self.SVC, "--image", "ai-gateway-admin-ui:0000000", *self.LIVE)
        self.assertEqual(r.returncode, 11, r.stdout + r.stderr)
        self.assertIn("there is no image ai-gateway-admin-ui:0000000 on this host", r.stdout)
        self.assertEqual(self.tags_moved(), [])
        self.assertNotIn(" up ", self.calls.read_text(encoding="utf-8"))

    def test_a_compose_tag_that_names_one_image_for_good_is_never_moved(self):
        self.admin_world(compose_image={"admin-ui": "ai-gateway-admin-ui:2fa9bb8"})
        r = self.run_it(*self.SVC, "--image", "ai-gateway-admin-ui:134b023", *self.LIVE)
        self.assertEqual(r.returncode, 11, r.stdout + r.stderr)
        self.assertIn("a tag that names one image for good", r.stdout)
        self.assertEqual(self.tags_moved(), [])

    def test_off_live_a_tag_another_project_runs_is_refused_and_live_is_only_told(self):
        # the test stack's admin-ui under live's image name (the repositories are shared)
        shared = ["ai-gateway-admin-ui-1 ai-gateway-admin-ui ai-gateway"]
        self.admin_world(ps_a=shared, compose_image={"admin-ui": "ai-gateway-admin-ui"})
        r = self.run_it(*self.SVC, "--image", "ai-gateway-admin-ui:134b023", "-p", "sran-test", "-f", "x.yml")
        self.assertEqual(r.returncode, 11, r.stdout + r.stderr)
        self.assertIn("is also what another compose project runs (ai-gateway-admin-ui-1)", r.stdout)
        self.assertEqual(self.tags_moved(), [], "a swap on the test stack moved the tag live starts from")
        self.admin_world(ps_a=["sran-test-admin-ui-1 ai-gateway-admin-ui:latest sran-test"])
        self.calls.unlink()
        r = self.run_it(*self.SVC, "--image", "ai-gateway-admin-ui:134b023", *self.LIVE)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("note: ai-gateway-admin-ui:latest is also what sran-test-admin-ui-1", r.stdout)
        self.assertEqual(len(self.tags_moved()), 2)

    def test_a_service_with_neither_door_nor_health_check_is_asked_whether_it_runs(self):
        self.world(services=["backup"], ps_q=["c0ld", "cn3w"], compose_image={"backup": "ai-gateway-backup:latest"},
                   images={"ai-gateway-backup:latest": {"id": OLD, "rev": "24491e5"},
                           "ai-gateway-backup:369345f": {"id": NEW, "rev": "369345f"}},
                   containers={"c0ld": {"image": OLD}, "cn3w": {"image": NEW}, "ai-gateway-backup": {"status": "running", "image": NEW}})
        r = self.run_it("scripts/ops/swap.sh", "backup", "running:ai-gateway-backup", "--image", "ai-gateway-backup:369345f",
                        "-p", "ai-gateway", "-f", "docker-compose.webchat.yml")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("(ceiling 20 s)", r.stdout)
        self.assertIn("tag sha256:" + "0" * 60 + "01d0 ai-gateway-backup:rollback-24491e5", "\n".join(self.tags_moved()))


class TheBackendDeployMovesOnlyItsOwnTag(Harness):
    def backend_world(self, compose_image: str, ps_a: list[str], **extra):
        w = dict(services=["backend", "ops-watch"], compose_image={"backend": compose_image}, ps_q=["c0ld", "cn3w"], ps_a=ps_a,
                 images={"ai-gateway-backend:latest": {"id": OLD, "rev": "67d9fc0"},
                         "ai-gateway-backend:134b023": {"id": NEW, "rev": "134b023"},
                         "ai-gateway-backend:rollback-67d9fc0": {"id": OLD, "rev": "67d9fc0"}},
                 containers={"c0ld": {"image": OLD, "rev": "67d9fc0"}, "cn3w": {"image": NEW, "rev": "134b023"},
                             "sran-test-backend-bridge": {}, "ai-gateway-backend-bridge": {}})
        w["images"].update(extra.pop("images", {}))
        w.update(extra)
        self.world(**w)

    def deploy(self, *args: str, **env: str):
        return self.run_it("scripts/deploy-backend.sh", *args, "--probe-url", "", **{"DEPLOY_MAINTENANCE_CMD": "", **env})

    def test_on_the_test_stack_the_deploy_moves_the_test_stacks_tag_and_never_lives(self):
        # (until AM24b: `docker tag <new> ai-gateway-backend:latest` whatever the compose files ran — the tag live's
        # backend and ops-watch start from)
        self.backend_world("ai-gateway-backend:drill", ["ai-gateway-backend-1 ai-gateway-backend ai-gateway",
                                                        "ai-gateway-ops-watch-1 ai-gateway-backend:latest ai-gateway"],
                           images={"ai-gateway-backend:drill": {"id": OLD, "rev": "67d9fc0"}})
        r = self.deploy("--image", "ai-gateway-backend:134b023", "-p", "sran-test", "-f", "docker-compose.test-stack.yml",
                        "--no-ops-watch")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        moved = [l.split()[-1] for l in self.tags_moved()]
        self.assertNotIn("ai-gateway-backend:latest", moved, "the test stack's deploy moved live's tag")
        self.assertIn("ai-gateway-backend:drill", moved)
        self.assertEqual(self.now()["images"]["ai-gateway-backend:latest"]["id"], OLD)

    def test_a_revisions_own_tag_is_refused_and_nothing_is_moved(self):
        self.backend_world("ai-gateway-backend:2fa9bb8", [])
        r = self.deploy("--image", "ai-gateway-backend:134b023", "-p", "sran-test", "-f", "docker-compose.test-stack.yml")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("a tag that names one image for good", r.stderr)
        self.assertEqual(self.tags_moved(), [])

    def test_off_live_the_tag_live_runs_is_refused_and_nothing_is_moved(self):
        self.backend_world("ai-gateway-backend:latest", ["ai-gateway-backend-1 ai-gateway-backend ai-gateway",
                                                         "ai-gateway-ops-watch-1 ai-gateway-backend:latest ai-gateway"])
        r = self.deploy("--image", "ai-gateway-backend:134b023", "-p", "sran-test", "-f", "docker-compose.test-stack.yml")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("is also what another compose project runs (ai-gateway-backend-1 ai-gateway-ops-watch-1)", r.stderr)
        self.assertEqual(self.tags_moved(), [])

    def test_on_live_it_moves_latest_as_before_keeps_the_way_back_and_records_both_services(self):
        self.backend_world("ai-gateway-backend:latest", ["ai-gateway-backend-1 ai-gateway-backend ai-gateway",
                                                         "sran-test-backend-1 ai-gateway-backend:latest sran-test"])
        r = self.deploy("--image", "ai-gateway-backend:134b023", "-p", "ai-gateway")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("note: ai-gateway-backend:latest is also what sran-test-backend-1", r.stdout)
        self.assertEqual([l.split()[-1] for l in self.tags_moved()],
                         ["ai-gateway-backend:rollback-67d9fc0", "ai-gateway-backend:latest"])
        rows = [l.split("\t") for l in self.record.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(f[1], f[2], f[3], f[5], f[6], f[7]) for f in rows],
                         [("deploy-backend", "ai-gateway", s, NEW, "134b023", "ai-gateway-backend:rollback-67d9fc0")
                          for s in ("backend", "ops-watch")])

    def test_rev_refuses_an_image_whose_label_names_another_commit(self):
        full = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        self.backend_world("ai-gateway-backend:latest", [], images={f"ai-gateway-backend:{full[:7]}": {"id": NEW, "rev": "deadbee"}})
        r = self.run_it("scripts/deploy-backend.sh", "--rev", "HEAD", "--dry-run", "-p", "ai-gateway")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(f"says it was built from deadbee, not {full}", r.stderr)


class TheRunbookCannotGoStaleUnseen(Harness):
    """The committed runbook, against a stand-in live built from its own state."""

    def setUp(self):
        super().setUp()
        self.text = book.BOOK.read_text(encoding="utf-8")
        self.state = book.state_of(self.text)
        self.assertIsNotNone(self.state, "docs/ops/ROLLBACK.md holds no state")
        self.page = self.tmp / "ROLLBACK.md"

    def live_from(self, state: dict, **change):
        """A stand-in live that runs exactly what `state` says; `change` = {service key: {field: value}} alters it."""
        containers, ps_a, images = {}, [], {}
        for k, e in state["services"].items():
            if e["kind"] not in ("backend", "swap", "with", "nothing-kept"):
                continue
            c = {**e, **change.get(k, {})}
            labels = {"com.docker.compose.service": e["name"], "com.docker.compose.project": e["project"],
                      "com.docker.compose.project.config_files": ",".join(e["files"]),
                      "org.opencontainers.image.revision": c.get("current_rev") or ""}
            cid = "sha256:" + (c.get("current_id") or "0" * 12) + "0" * 52
            containers[e["container"]] = {"line": json.dumps(labels) + f"|{cid}|{e.get('ref', '')}|running|0|healthy"}
            ps_a.append(f"{e['project']} {e['container']}")
            for t in ("current_tag", "rollback_tag"):
                if e.get(t):
                    images[e[t]] = {"id": cid}
        for t in change.get("_gone", []):
            images.pop(t, None)
        self.world(images=images, containers=containers, ps_a=ps_a)

    def check(self, page_text: str, docker: str = "fake_docker.py", **env) -> subprocess.CompletedProcess:
        # (Python on Windows runs docker.exe, never a shell stand-in on PATH: the stand-in is named to the tool)
        self.page.write_text(page_text, encoding="utf-8", newline="\n")
        stand_in = f'"{Path(sys.executable).as_posix()}" "{(self.tmp / docker).as_posix()}"'
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "ops" / "rollback_book.py"), "check"], cwd=ROOT,
                              env={**self.env, "ROLLBACK_BOOK": str(self.page), "PYTHONIOENCODING": "utf-8",
                                   "ROLLBACK_BOOK_DOCKER": stand_in, **env},
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240)

    def setUp_fake_for_containers(self):
        # the stand-in answers `docker ps -a --filter label=…project=P` and `docker inspect --format … <name>` for the
        # runbook's reads: extend the stand-in with those two answers
        fake = (self.tmp / "fake_docker.py").read_text(encoding="utf-8")
        fake = fake.replace('if cmd == "inspect":', '''if cmd == "ps" and any(x.startswith("label=com.docker.compose.project=") for x in a):
    p = next(x for x in a if x.startswith("label=com.docker.compose.project=")).split("=", 2)[2]
    out("\\n".join(l.split(" ", 1)[1] for l in w.get("ps_a", []) if l.split(" ", 1)[0] == p))
if cmd == "inspect" and "Labels" in fmt():
    c = w.get("containers", {}).get(a[-1])
    if c is None:
        sys.exit(1)
    out(c["line"])
if cmd == "inspect":''', 1)
        (self.tmp / "fake_docker.py").write_text(fake, encoding="utf-8")

    def run_check(self, page_text: str, **change) -> subprocess.CompletedProcess:
        self.setUp_fake_for_containers()
        self.live_from(self.state, **change)
        return self.check(page_text)

    def lines(self, r) -> dict:
        return {l[10:].split(" (")[0].split(" — ")[0]: l[:14].split()[1] for l in r.stdout.splitlines() if l.startswith("rollback: ")}

    def test_green_when_live_runs_what_it_says(self):
        r = self.run_check(self.text)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(set(self.lines(r).values()), {"ok", "skip"}, r.stdout)
        self.assertEqual(r.stdout.count("rollback: "), 5)

    def test_red_when_a_compose_service_has_no_line(self):
        key = next(k for k, e in self.state["services"].items() if e["kind"] == "swap")
        s = json.loads(json.dumps(self.state))
        del s["services"][key]
        r = self.run_check(book.render(s))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(f"no line: {key}", r.stdout)

    def test_red_when_the_page_was_edited_by_hand(self):
        edited = self.text.replace("## Where the tags come from", "## Where tags come from", 1)
        self.assertNotEqual(edited, self.text)
        r = self.run_check(edited)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("it was edited by hand", r.stdout)

    def test_red_when_a_tag_it_names_is_gone(self):
        e = next(e for e in self.state["services"].values() if e["kind"] == "backend")
        r = self.run_check(self.text, _gone=[e["rollback_tag"]])
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(f"not on this host: {e['rollback_tag']}", r.stdout)

    def test_red_when_live_runs_something_else(self):
        key, e = next((k, e) for k, e in self.state["services"].items() if e["name"] == "chat-edge-api" and e["kind"] == "swap")
        r = self.run_check(self.text, **{key: {"current_rev": "f01f1ee"}})
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(f"chat-edge-api runs f01f1ee, the page says {e['current_rev']}", r.stdout)

    def test_red_when_a_deploy_recorded_since_says_something_else(self):
        e = next(e for e in self.state["services"].values() if e["kind"] == "backend")
        later = "2099-01-01T00:00:00Z"
        self.record.write_text("\t".join([later, "deploy-backend", e["project"], "backend", "ai-gateway-backend:9999999",
                                          "sha256:x", "9999999", e["rollback_tag"], "ok"]) + "\n", encoding="utf-8")
        r = self.run_check(self.text)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(f"backend deployed at 9999999 by deploy-backend at {later}", r.stdout)

    def test_without_docker_the_host_checks_say_so_and_are_skipped(self):
        self.world(images={})
        (self.tmp / "no_docker.py").write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
        r = self.check(self.text, docker="no_docker.py", DEPLOY_RECORD=(self.tmp / "none.tsv").as_posix())
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(r.stdout.count("rollback: skip"), 3, r.stdout)
        self.assertIn("no Docker here", r.stdout)

    def test_the_gates_ledger_counts_its_checks(self):
        got = ledger.count("rollback_book", "rollback: ok   a\nrollback: skip b — no Docker here\nrollback: RED  c\n", 1)
        self.assertEqual((got["ran"], got["skipped"]), (3, 1))
        gate = (ROOT / "scripts" / "gate.sh").read_text(encoding="utf-8")
        self.assertIn("lane rollback_book 300 python scripts/ops/rollback_book.py check", gate)
        self.assertIn(" rollback_book ", gate.split("LANES=", 1)[1].split("\n", 1)[0])


if __name__ == "__main__":
    unittest.main()
