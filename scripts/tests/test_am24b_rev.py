"""AM24b Part 1 — one rule for the short revision, and every tool that looks an image up by revision finds it.

On 18 Oct 2026 deploy-backend.sh built the backend image (tagged `134b023`, seven characters: the builds' rule,
`git rev-parse --short=7`) and then asked Docker for `134b0238` (`git rev-parse --short`, git's default length, eight
here since the repository grew); it stopped with exit 125 before touching anything. The rule now
(scripts/ops/rev.sh): builds tag with `rev_tag`; a lookup resolves the revision to its full commit and takes the image
whose tag is a prefix of it, seven characters or more (`rev_image`). The defect made on purpose (CARD-RULES §6d): an
image tagged at one length, asked for at another — a full SHA, seven, eight, a tag, HEAD.

With stand-ins for Docker (nothing real is started):

* scripts/ops/rev.sh, in a repository of its own with a git tag: every form of a revision finds the image; an image
  tagged at eight is found from seven; a tag that is no prefix of the commit is never taken; a commit with no image is
  refused by name;
* the backend's deploy, `--rev` given every form, reaches its deploy with the seven-character image (it stopped at the
  migration check with exit 125 before AM24b);
* the test stack's `check`, given eight characters (or a full SHA, or HEAD) for images tagged seven, finds the stack
  it started (before AM24b: "runs ...:<7>, not ...:<8>", exit 7 -- the seam AM19b met on 14 Oct); its `up`, `edge`,
  `runner`, a hand-given TS_BACKEND_TAG and the web stand-ins' `web` take a revision the same way;
* the test clients and the build scripts carry the same rule; the swap takes no revision at all.

    python -m unittest discover -s scripts/tests -p "test_am24b_rev.py" -v
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash")

# A stand-in for Docker: images exist only as the "repo:tag" lines of $FAKE_IMAGES.
DOCKER = r'''#!/bin/sh
echo "$@" >> "$FAKE_CALLS"
has() { grep -qx "$1" "$FAKE_IMAGES" 2>/dev/null; }
case "$1" in
  compose)
    case " $* " in
      *" config --format json "*) echo '{"services":{"backend":{"image":"ai-gateway-backend:latest"}}}'; exit 0 ;;
      *" ps -q backend "*) echo "0ldbackend0001"; exit 0 ;;
    esac
    exit 0 ;;
  image)
    case "$2" in
      ls)
        repo="$3"; grep "^$repo:" "$FAKE_IMAGES" 2>/dev/null | sed "s#^$repo:##"; exit 0 ;;
      inspect)
        shift 2
        fmt=""; [ "$1" = "-f" ] && { fmt="$2"; shift 2; }
        for ref; do has "$ref" || { echo "Error: No such image: $ref" >&2; exit 1; }; done
        case "$fmt" in *revision*) echo "${FAKE_REV:-}" ;; *) echo "sha256:fake" ;; esac
        exit 0 ;;
    esac ;;
  ps) cut -d' ' -f1 "$FAKE_CONTAINERS" 2>/dev/null; exit 0 ;;
  inspect)
    shift; fmt=""
    [ "$1" = "-f" ] && { fmt="$2"; shift 2; }
    img=$(grep "^$1 " "$FAKE_CONTAINERS" 2>/dev/null | cut -d' ' -f2)
    case "$fmt" in *State.Status*) [ -n "$img" ] && { echo "/$1 running 0 healthy $img"; exit 0; } ;; esac
    echo "sha256:0ldimage"; exit 0 ;;
  run)
    for a; do last="$a"; done
    img=""; for a; do case "$a" in ai-gateway-*) img="$a"; break ;; esac; done
    has "$img" || { echo "Unable to find image '$img' locally" >&2; exit 125; }
    echo "aaaa1111 (head)"; exit 0 ;;
  exec) echo "aaaa1111 (head)"; exit 0 ;;
esac
exit 0
'''
POWERSHELL = "#!/bin/sh\necho 'Online 1 100'\n"


def git(*args, cwd=ROOT) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@unittest.skipIf(BASH is None or "system32" in (BASH or "").lower(), "no POSIX bash on PATH")
class TheShortRevision(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="am24b-rev-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        b = self.tmp / "bin"
        b.mkdir()
        for name, body in (("docker", DOCKER), ("powershell", POWERSHELL)):
            (b / name).write_text(body, encoding="utf-8", newline="\n")
            os.chmod(b / name, 0o755)
        self.images = self.tmp / "images.txt"
        self.env = {**os.environ, "PATH": str(b) + os.pathsep + os.environ.get("PATH", ""),
                    "FAKE_IMAGES": self.images.as_posix(), "FAKE_CALLS": (self.tmp / "calls.txt").as_posix(),
                    "FAKE_CONTAINERS": (self.tmp / "containers.txt").as_posix()}
        self.env.pop("RUN_ON_BATTERY", None)
        self.env["MSYS_NO_PATHCONV"] = "1"  # as the gate runs them; git cannot read a /c/... path then

    def bash(self, script: str, cwd=ROOT, **env) -> subprocess.CompletedProcess:
        return subprocess.run([BASH, "-c", script], cwd=cwd, env={**self.env, **env}, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=180)

    def lookup(self, repo_dir: Path, rev: str) -> subprocess.CompletedProcess:
        rev_sh = (ROOT / "scripts" / "ops" / "rev.sh").as_posix()
        return self.bash(f'ROOT="{repo_dir.as_posix()}"; . "{rev_sh}"; rev_image ai-gateway-backend "{rev}"', cwd=repo_dir)

    # ── scripts/ops/rev.sh, in a repository of its own ───────────────────────────────────────────────────

    def make_repo(self) -> tuple[Path, str]:
        repo = self.tmp / "repo"
        repo.mkdir()
        for a in (["init", "-q"], ["config", "user.email", "t@example.test"], ["config", "user.name", "t"]):
            git(*a, cwd=repo)
        (repo / "a.txt").write_text("one\n", encoding="utf-8")
        git("add", "a.txt", cwd=repo)
        git("commit", "-q", "-m", "one", cwd=repo)
        (repo / "a.txt").write_text("two\n", encoding="utf-8")
        git("commit", "-q", "-am", "two", cwd=repo)
        git("tag", "v1", cwd=repo)
        return repo, git("rev-parse", "HEAD", cwd=repo)

    def test_every_form_of_a_revision_finds_the_image_tagged_seven(self):
        repo, full = self.make_repo()
        self.images.write_text(f"ai-gateway-backend:latest\nai-gateway-backend:{full[:7]}\n", encoding="utf-8")
        for rev in (full, full[:7], full[:8], full[:12], "HEAD", "v1"):
            r = self.lookup(repo, rev)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, f"ai-gateway-backend:{full[:7]}"), f"{rev}: {r.stderr}")

    def test_an_image_tagged_at_eight_is_found_from_seven_and_no_other_tag_is_taken(self):
        repo, full = self.make_repo()
        parent = git("rev-parse", "HEAD~1", cwd=repo)
        self.images.write_text(f"ai-gateway-backend:{full[:8]}\nai-gateway-backend:{parent[:7]}\n"
                               f"ai-gateway-backend:{full[:6]}\nai-gateway-backend:rollback-{full[:7]}\n", encoding="utf-8")
        r = self.lookup(repo, full[:7])
        self.assertEqual(r.stdout.strip(), f"ai-gateway-backend:{full[:8]}", r.stderr)

    def test_a_commit_with_no_image_and_a_revision_git_does_not_know_are_refused_by_name(self):
        repo, full = self.make_repo()
        self.images.write_text("ai-gateway-backend:latest\n", encoding="utf-8")
        r = self.lookup(repo, "HEAD")
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"no image of ai-gateway-backend built from HEAD ({full})", r.stderr)
        r = self.lookup(repo, "no-such-revision")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not a commit git knows: no-such-revision", r.stderr)

    # ── the backend's deploy ─────────────────────────────────────────────────────────────────────────────

    def test_the_backend_deploy_finds_the_image_whatever_form_the_revision_takes(self):
        full = git("rev-parse", "HEAD")
        self.images.write_text(f"ai-gateway-backend:latest\nai-gateway-backend:{full[:7]}\n", encoding="utf-8")
        for rev in (full, full[:7], full[:8], "HEAD"):
            r = subprocess.run([BASH, "scripts/deploy-backend.sh", "--rev", rev, "--dry-run"], cwd=ROOT,
                               env={**self.env, "FAKE_REV": full[:7]},
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
            self.assertEqual(r.returncode, 0, f"--rev {rev}: exit {r.returncode}\n{r.stdout}\n{r.stderr}")
            self.assertIn(f"deploying ai-gateway-backend:{full[:7]} ", r.stdout, rev)

    # ── the test stack ───────────────────────────────────────────────────────────────────────────────────

    def test_the_test_stacks_check_given_any_revision_finds_the_stack_it_started(self):
        # 14 Oct 2026 (AM19b) and 18 Oct: eight characters asked for images tagged seven. Before AM24b this said
        # "RED: sran-test-chat-edge-api-1 runs ai-gateway-chat-edge-api:<7>, not ai-gateway-chat-edge-api:<8>", exit 7.
        full = git("rev-parse", "HEAD")
        t = full[:7]
        self.images.write_text("".join(f"ai-gateway-{r}:{t}\n" for r in ("chat-edge-api", "agent-runner", "backend")),
                               encoding="utf-8")
        (self.tmp / "containers.txt").write_text(
            f"sran-test-chat-edge-api-1 ai-gateway-chat-edge-api:{t}\nsran-test-agent-runner ai-gateway-agent-runner:{t}\n"
            f"sran-test-backend-1 ai-gateway-backend:{t}\n", encoding="utf-8")
        for rev in (full[:8], full, "HEAD", t):
            r = subprocess.run([BASH, "scripts/agent-market/test_stack.sh", "check", rev], cwd=ROOT, env=self.env,
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
            self.assertEqual(r.returncode, 0, f"check {rev}: exit {r.returncode}\n{r.stdout}{r.stderr}")
            self.assertIn(f"edge and runner at {t}, backend at {t}", r.stdout, rev)
        r = subprocess.run([BASH, "scripts/agent-market/test_stack.sh", "check", full[:8]], cwd=ROOT,
                           env={**self.env, "TS_BACKEND_TAG": full[:9]}, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(r.returncode, 0, "a backend tag given by hand at nine characters: " + r.stdout + r.stderr)

    def test_the_test_stacks_up_takes_the_fetchers_tag_as_any_revision(self):
        # gate-found at fe49e178: `gate.sh stack <8 characters>` hands AM_FETCHER_TAG on as typed, and the stack said
        # "RED: not on this host: … ai-gateway-web-fetcher:fe49e178", exit 6, for a fetcher tagged fe49e17. Here every
        # image is on the host but the backend's: the stack refuses naming that one only.
        full = git("rev-parse", "HEAD")
        t = full[:7]
        self.images.write_text("".join(f"ai-gateway-{r}:{t}\n" for r in ("chat-edge-api", "agent-runner", "web-fetcher")),
                               encoding="utf-8")
        r = subprocess.run([BASH, "scripts/agent-market/test_stack.sh", "up", full[:8]], cwd=ROOT,
                           env={**self.env, "AM_FETCHER_TAG": full[:8], "TS_BACKEND_TAG": "nothere"},
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(r.returncode, 6, r.stdout + r.stderr)
        self.assertIn("RED: not on this host: ai-gateway-backend:nothere —", r.stdout)

    def test_the_test_stacks_tag_given_as_any_revision_is_the_tag_its_images_carry(self):
        full = git("rev-parse", "HEAD")
        self.images.write_text(f"ai-gateway-chat-edge-api:{full[:7]}\nai-gateway-agent-runner:{full[:7]}\n", encoding="utf-8")
        script = (f'ROOT="{ROOT.as_posix()}"; . scripts/ops/rev.sh; '
                  f'for r in {full} {full[:8]} {full[:7]} HEAD rollback-f01f1ee; do rev_existing_tag ai-gateway-chat-edge-api "$r"; done')
        r = self.bash(script)
        self.assertEqual(r.stdout.split(), [full[:7]] * 4 + ["rollback-f01f1ee"], r.stderr)
        src = (ROOT / "scripts" / "agent-market" / "test_stack.sh").read_text(encoding="utf-8")
        for sub in ('TAG="$(rev_existing_tag ai-gateway-chat-edge-api "${2:?tag}")"',
                    'check "$(rev_existing_tag ai-gateway-chat-edge-api "${2:?tag}")"',
                    'edge "$(rev_existing_tag ai-gateway-chat-edge-api "${2:?tag}")"',
                    'runner "$(rev_existing_tag ai-gateway-agent-runner "${2:?tag}")"',
                    'TS_BACKEND_TAG="$(rev_existing_tag ai-gateway-backend "$TS_BACKEND_TAG")"',
                    'AM_FETCHER_TAG="$(rev_existing_tag ai-gateway-web-fetcher "$AM_FETCHER_TAG")"'):
            self.assertIn(sub, src, "the test stack takes a revision as given in one of its commands")
        web = (ROOT / "scripts" / "agent-market" / "test_stack_web.sh").read_text(encoding="utf-8")
        self.assertIn('TAG="$(rev_existing_tag ai-gateway-web-fetcher "${2:?', web, "the web stand-ins take a tag as given")

    # ── the others: the same rule, or no revision at all ─────────────────────────────────────────────────

    def test_every_build_and_the_test_clients_tag_by_one_function_and_the_swap_takes_no_revision(self):
        for f in ("scripts/build-images.sh", "scripts/agent-market/build.sh", "scripts/agent-market/build-fetcher.sh",
                  "scripts/agent-market/build-converter.sh", "scripts/agent-market/test_clients.sh",
                  "scripts/deploy-backend.sh", "scripts/agent-market/test_stack.sh",
                  "scripts/agent-market/test_stack_web.sh"):
            src = (ROOT / f).read_text(encoding="utf-8")
            code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
            self.assertIn('. "$ROOT/scripts/ops/rev.sh"', code, f)
            self.assertNotIn("rev-parse --short", code, f"{f} makes a short revision of its own")
        swap = (ROOT / "scripts" / "ops" / "swap.sh").read_text(encoding="utf-8")
        self.assertNotIn("rev-parse", swap, "the swap looks an image up by revision: it must use scripts/ops/rev.sh")

    def test_the_gates_ledger_keys_a_commit_by_its_first_seven_characters(self):
        gate = (ROOT / "scripts" / "gate.sh").read_text(encoding="utf-8")
        self.assertIn('git rev-parse --verify "${GATE_FOR_SHA}^{commit}" | cut -c1-7', gate)
        self.assertIn("git rev-parse HEAD | cut -c1-7", gate)
        ledger = (ROOT / "scripts" / "gate" / "ledger.py").read_text(encoding="utf-8")
        self.assertIn('_git("rev-parse", "HEAD")', ledger)


if __name__ == "__main__":
    unittest.main()
