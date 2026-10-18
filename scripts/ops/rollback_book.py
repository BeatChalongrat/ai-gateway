#!/usr/bin/env python3
"""AM24b — the rollback runbook, docs/ops/ROLLBACK.md: written from what live runs, and checked against it.

    python scripts/ops/rollback_book.py write   # on the host live runs on, after every deploy; then commit the runbook
    python scripts/ops/rollback_book.py check   # the gate's rollback_book lane

Nobody edits the runbook. `write` makes it from three things: the compose files (every service, every profile — each
gets a line); what live runs (each container's image, its revision label, the tags of that image, and the rollback
images kept on this host, <repository>:rollback-<revision>); and scripts/ops/results/deploys.tsv, the line swap.sh
and deploy-backend.sh add for every deploy. The state it was written from is kept at the end of the runbook, so
`check` can tell anywhere that the text is what `write` makes of it.

`check` is red when a service in the compose files has no line; when the text is not what `write` makes of its own
state (edited by hand); when a tag it names does not exist on this host; when live runs something other than what it
calls current — at all, or after a deploy recorded its own tags. A runbook that silently goes stale is a check that
cannot fail. Without Docker (a clone, CI) the checks that need this host say so and are skipped.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
BOOK = Path(os.environ.get("ROLLBACK_BOOK") or ROOT / "docs" / "ops" / "ROLLBACK.md")
RECORD = Path(os.environ.get("DEPLOY_RECORD") or ROOT / "scripts" / "ops" / "results" / "deploys.tsv")
STATE_OPEN, STATE_CLOSE = "<!-- rollback-book-state", "-->"
REV_LABEL = "org.opencontainers.image.revision"
# (ROLLBACK_BOOK_DOCKER: the script's own test — a stand-in for Docker, as a command line)
DOCKER = shlex.split(os.environ["ROLLBACK_BOOK_DOCKER"]) if os.environ.get("ROLLBACK_BOOK_DOCKER") else ["docker"]

# How live starts each set of compose files (its containers' label com.docker.compose.project.config_files).
SETS = [
    ("ai-gateway", ("docker-compose.yml", "docker-compose.override.yml"), ()),
    ("ai-gateway", ("docker-compose.webchat.yml",), ()),
    ("sran-agent-market", ("docker-compose.agent-market.yml",), ("--profile", "agent-market")),
]
# A service a person reaches through a door: the swap measures the door. Any other is measured by its container's
# health check, or, with none, by whether it runs.
DOORS = {"chat-edge-api": "https://localhost:4000/health", "web-chat": "http://127.0.0.1:23002/",
         "admin-ui": "http://127.0.0.1:23000/login", "portal": "http://127.0.0.1:23001/"}
# The order a card's deploy goes in (AM-14: the edge learns new engine words before the runner changes); back in
# reverse, so every state on the way back is one the deploy already passed through.
DEPLOY_ORDER = ["chat-edge-api", "agent-runner", "web-chat", "admin-ui", "backend"]
MOVES_WITH = {"ops-watch": "backend"}  # one image; deploy-backend.sh moves both
NOT_UNDONE = {
    "backend": "the schema migrations it applied to the gateway's database (the old image runs on the newer schema: "
               "the deploy refuses an image whose migration head differs unless told — `--allow-migrations` when the "
               "migration only added, otherwise `--stop-first`); files people uploaded (volume "
               "`ai-gateway_backend_uploads`); usage and credits recorded; e-mails sent.",
    "chat-edge-api": "migrations applied to the chat database (applied by hand; they stay); messages, files and "
                     "memory stored; credits charged for turns.",
    "agent-runner": "Agent Market's schema migrations (the runner applies them when it starts; the old runner runs on "
                    "the newer schema — additive only); the engine each installed version registered (a version "
                    "keeps its engine); runs' records, files delivered, credits charged, e-mails sent.",
    "web-chat": "nothing it stored — it stores nothing; a page open in a browser keeps the code it loaded until it "
                "reloads.",
    "admin-ui": "settings saved and audit entries written through it.",
    "portal": "keys and settings saved through it.",
    "gateway": "answers already in its response cache (kept up to an hour) and the usage it logged.",
    "doc-converter": "files already converted, and what was delivered from them.",
    "web-fetcher": "pages already fetched, and what was delivered from them.",
}
NOT_UNDONE_ANY = "anything it already wrote, stored or sent."


# ── reading ──────────────────────────────────────────────────────────────────────────────────────────────────────

def sh(*args: str) -> tuple[int, str]:
    if args and args[0] == "docker":
        args = (*DOCKER, *args[1:])
    try:
        r = subprocess.run(list(args), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return r.returncode, r.stdout.replace("\r", "").strip()


def docker_here() -> bool:
    return sh("docker", "version", "--format", "{{.Server.Version}}")[0] == 0


def compose_services() -> list[dict]:
    """Every service of every set of compose files live is started from, every profile, as the files say."""
    out = []
    for project, files, extra in SETS:
        merged: dict[str, dict] = {}
        for f in files:
            doc = yaml.safe_load((ROOT / f).read_text(encoding="utf-8")) or {}
            for name, svc in (doc.get("services") or {}).items():
                merged.setdefault(name, {}).update(svc or {})
        for name, svc in merged.items():
            out.append({"key": f"{files[0]}:{name}", "name": name, "project": project, "files": list(files),
                        "extra": list(extra), "image": svc.get("image") or "", "built": "build" in svc,
                        "profiles": list(svc.get("profiles") or [])})
    return out


def containers(project: str) -> list[dict]:
    code, names = sh("docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={project}",
                     "--format", "{{.Names}}")
    found = []
    for n in names.split() if code == 0 else []:
        # only labels, image and state are read — never the environment
        code, line = sh("docker", "inspect", "--format",
                        '{{json .Config.Labels}}|{{.Image}}|{{.Config.Image}}|{{.State.Status}}|{{.State.ExitCode}}|'
                        '{{with index .State "Health"}}{{.Status}}{{end}}', n)
        if code != 0:
            continue
        labels, image_id, ref, status, exit_code, health = line.rsplit("|", 5)
        labels = json.loads(labels or "{}") or {}
        files = [p.replace("\\", "/").rsplit("/", 1)[-1]
                 for p in (labels.get("com.docker.compose.project.config_files") or "").split(",") if p]
        found.append({"name": n, "service": labels.get("com.docker.compose.service", ""), "files": sorted(files),
                      "id": image_id, "ref": ref, "status": status, "exit": exit_code, "health": health,
                      "rev": labels.get(REV_LABEL, "")})
    return found


def repo_of(ref: str) -> str:
    last = ref.rsplit("/", 1)[-1]
    return ref.rsplit(":", 1)[0] if ":" in last else ref


def image_tags(repo: str) -> list[dict]:
    code, out = sh("docker", "image", "ls", repo, "--format", "{{.Tag}}|{{.ID}}|{{.CreatedAt}}")
    tags = []
    for line in out.splitlines() if code == 0 else []:
        tag, short_id, created = line.split("|", 2)
        if tag != "<none>":
            tags.append({"tag": tag, "id": short_id.replace("sha256:", ""), "created": created})
    return tags


def image_rev(ref: str) -> str:
    code, out = sh("docker", "image", "inspect", "--format",
                   '{{with .Config.Labels}}{{index . "' + REV_LABEL + '"}}{{end}}', ref)
    return out if code == 0 and out != "<no value>" else ""


def records() -> list[dict]:
    """scripts/ops/results/deploys.tsv: time, tool, project, service, image asked, image id, revision, rollback, verdict."""
    rows = []
    try:
        text = RECORD.read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        f = line.split("\t")
        if len(f) >= 9:
            rows.append(dict(zip(("time", "tool", "project", "service", "image", "id", "rev", "rollback", "verdict"), f)))
    return rows


def last_record(rows: list[dict], project: str, service: str) -> dict | None:
    mine = [r for r in rows if r["project"] == project and r["service"] == service]
    return max(mine, key=lambda r: r["time"]) if mine else None


# ── the state: what live runs, service by service ───────────────────────────────────────────────────────────────

def build_state() -> dict:
    rows = records()
    live: dict[str, list[dict]] = {}
    for project, _, _ in SETS:
        live.setdefault(project, containers(project))
    services = {}
    for s in compose_services():
        mine = [c for c in live[s["project"]] if c["service"] == s["name"] and c["files"] == sorted(s["files"])]
        elsewhere = [c for c in live[s["project"]] if c["service"] == s["name"] and c["files"] != sorted(s["files"])]
        e = {"name": s["name"], "project": s["project"], "files": s["files"], "extra": s["extra"]}
        running = [c for c in mine if c["status"] == "running"]
        if not running:
            if mine and mine[0]["status"] == "exited" and mine[0]["exit"] == "0":
                e["kind"] = "oneshot"
            elif elsewhere:
                e["kind"], e["elsewhere"] = "elsewhere", elsewhere[0]["files"][0]
            else:
                e["kind"], e["profiles"] = "off", s["profiles"]
            services[s["key"]] = e
            continue
        c = running[0]
        e["container"], e["ref"] = c["name"], c["ref"]
        repo = repo_of(c["ref"] or s["image"])
        e["repo"] = repo
        if s["name"] in MOVES_WITH:
            e["kind"], e["with"] = "with", MOVES_WITH[s["name"]]
            e["current_rev"] = c["rev"]
            services[s["key"]] = e
            continue
        if not (s["built"] or repo.startswith("ai-gateway-")):
            e["kind"], e["pinned"] = "pinned", s["image"] or c["ref"]
            services[s["key"]] = e
            continue
        tags = image_tags(repo)
        cid = c["id"].replace("sha256:", "")
        rev = c["rev"]
        same = [t for t in tags if cid.startswith(t["id"])]
        cur = ([t for t in same if rev and t["tag"] != "latest" and rev.startswith(t["tag"])]
               or [t for t in same if not t["tag"].startswith("rollback-") and t["tag"] != "latest"]
               or [t for t in same if t["tag"] == "latest"] or same)
        e["current_tag"] = f"{repo}:{cur[0]['tag']}" if cur else ""
        e["current_rev"], e["current_id"] = rev, cid[:12]
        back = [t for t in tags if t["tag"].startswith("rollback-") and not cid.startswith(t["id"])]
        rec = last_record(rows, s["project"], s["name"])
        e["deployed"] = f"{rec['tool']}, {rec['time']}" if rec else ""
        chosen = None
        if rec and rec["rollback"].startswith(repo + ":"):
            chosen = next((t for t in back if f"{repo}:{t['tag']}" == rec["rollback"]), None)
        if chosen is None and back:
            chosen = max(back, key=lambda t: (t["created"], t["tag"]))
        if chosen is None:
            e["kind"] = "nothing-kept"
        else:
            e["kind"] = "backend" if s["name"] == "backend" else "swap"
            e["rollback_tag"] = f"{repo}:{chosen['tag']}"
            e["rollback_label"] = image_rev(e["rollback_tag"])
            e["rollback_rev"] = e["rollback_label"] or chosen["tag"].removeprefix("rollback-")
            e["rollback_id"] = chosen["id"][:12]
            e["rollback_from"] = "the deploy record" if rec and rec["rollback"] == e["rollback_tag"] else \
                "the newest rollback image kept on this host"
            e["probe"] = DOORS.get(s["name"]) or (f"health:{c['name']}" if c["health"] else f"running:{c['name']}")
        services[s["key"]] = e
    return {"written": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "services": services}


# ── the runbook ─────────────────────────────────────────────────────────────────────────────────────────────────

def compose_args(e: dict) -> str:
    return " ".join([f"-p {e['project']}"] + [f"-f {f}" for f in e["files"]] + e["extra"])


def command(e: dict, tag: str) -> str:
    if e["kind"] == "backend":
        return f"bash scripts/deploy-backend.sh --image {tag} -p {e['project']}"
    return f"bash scripts/ops/swap.sh {e['name']} {e['probe']} --image {tag} {compose_args(e)}"


def forward_tag(e: dict) -> str:
    """The tag that names today's image after a rollback: `latest` moves with it, so the image tagged only `latest`
    is the one the rollback keeps as rollback-<revision>."""
    if e["current_tag"].endswith(":latest") and e["current_rev"]:
        return f"{e['repo']}:rollback-{e['current_rev']}"
    return e["current_tag"]


def order_key(e: dict) -> tuple:
    n = e["name"]
    return (0, -DEPLOY_ORDER.index(n)) if n in DEPLOY_ORDER else (1, n)


def render(state: dict) -> str:
    S = state["services"]
    built = sorted((e for e in S.values() if e["kind"] in ("backend", "swap")), key=order_key)
    withs = {e["with"]: e for e in S.values() if e["kind"] == "with"}
    L = ["# Rolling back a service", "",
         f"Written by `python scripts/ops/rollback_book.py write` from what live ran at {state['written']} — never "
         "edited by hand: the gate's `rollback_book` lane is red when this page no longer says what runs, when a tag "
         "it names is gone, or when a service has no line. Run every command from the root of this repository, on "
         "the host live runs on (Git Bash on Windows). A rollback is a deploy: it refuses on battery like one "
         "(`scripts/ops/power.sh`).", "",
         "Each block: the command that goes back, what runs now, how to see it worked, the command forward again, "
         "and what going back does **not** undo. The image it goes back to is the one that ran before — kept by the "
         "deploy that replaced it.", ""]
    lead = [e for e in built if e["name"] in DEPLOY_ORDER]
    rest = [e for e in built if e["name"] not in DEPLOY_ORDER]
    for title, group in (("## The services a card deploys — in the order to go back", lead),
                         ("## Every other service built here", rest)):
        if not group:
            continue
        L += [title, ""]
        for e in group:
            w = withs.get(e["name"])
            name = e["name"] + (f" + {w['name']}" if w else "")
            conts = [e["container"]] + ([w["container"]] if w else [])
            L += [f"### {name} — now {e['current_rev'] or '?'}, back to {e['rollback_rev']}", "",
                  "```bash", command(e, e["rollback_tag"]), "```", "",
                  f"- **Now:** `{e['current_tag']}`, revision {e['current_rev'] or '(none on the image)'}"
                  + (f" — {e['deployed']}." if e["deployed"] else " — deployed before the deploy record began.")
                  + f" Back to `{e['rollback_tag']}` ({e['rollback_from']}).",
                  "- **It worked when** " + (
                      f"the deploy's last lines say `done: backend … healthy revision {e['rollback_rev']}` and "
                      "`probe: … 0 failed`; " if e["kind"] == "backend" else
                      "the swap's last line has no `RED`; ")
                  + (f"this prints `{e['rollback_label']}`" + (" for each" if len(conts) > 1 else "")
                     + ": `docker inspect -f '{{index .Config.Labels \"" + REV_LABEL + "\"}}' " + " ".join(conts) + "`"
                     if e.get("rollback_label") else
                     f"this prints an id starting `sha256:{e['rollback_id']}` (the image has no revision label): "
                     "`docker inspect -f '{{.Image}}' " + " ".join(conts) + "`")
                  + "; and `bash scripts/smoke.sh` ends `GREEN` (right after a deploy, give the gateway 90 s "
                  "quiet first — its limit answers 429).",
                  f"- **Forward again:** `{command(e, forward_tag(e))}`"
                  + (" — the rollback keeps today's image under that name (`latest` moves with it)."
                     if forward_tag(e) != e["current_tag"] else ""),
                  f"- **Not undone:** {NOT_UNDONE.get(e['name'], NOT_UNDONE_ANY)}", ""]
    together = {}
    for e in built:
        if e["current_rev"]:
            together.setdefault(e["current_rev"], []).append(e["name"] + (" + ops-watch" if e["name"] in withs else ""))
    L += ["## More than one service: the order, and what moves together", "",
          "Go back in the reverse of the order a deploy goes in — " + ", ".join(reversed(DEPLOY_ORDER)) +
          " — so every state on the way back is one the deploy already passed through. Any other service after "
          "them, in any order.", "",
          "- **backend and ops-watch** are one image: `deploy-backend.sh` moves both. Never swap ops-watch alone.",
          "- **agent-runner and chat-edge-api** move together, the runner back first: the edge drives the runner's "
          "engines and speaks its words.",
          "- **web-chat goes back with chat-edge-api:** the page calls the edge's routes; a page newer than its edge "
          "can call one that is not there."]
    for rev, names in sorted(together.items()):
        if len(names) > 1:
            L.append(f"- **Deployed together at {rev}:** {', '.join(names)} — a fault in that deploy takes all of "
                     "them back, each to its own image above.")
    L += ["", "## Where the tags come from", "",
          "- `<repository>:<revision>` — seven characters of the commit, by the builds: `bash scripts/gate.sh build "
          "<sha>`, `scripts/build-images.sh --rev <rev> <service>`, `scripts/agent-market/build*.sh` "
          "(`scripts/ops/rev.sh` is the rule; `--rev` takes anything git accepts).",
          "- `<repository>:rollback-<revision>` — the image a service ran when a deploy replaced it, kept by that "
          "deploy: `deploy-backend.sh` always, `swap.sh --image` since AM-24b (by hand before).",
          "- The tag the compose file runs (`latest` here) — what a service starts from; the deploy points it at the "
          "new image.",
          "- Every deploy adds a line to `scripts/ops/results/deploys.tsv` on this host: time, tool, project, "
          "service, image, revision, the rollback image kept. After a deploy: `python scripts/ops/rollback_book.py "
          "write`, then commit this page.", ""]
    be = next((e for e in built if e["kind"] == "backend"), None)
    edge = next((e for e in built if e["name"] == "chat-edge-api"), None)
    if be:
        drill = (f"TS_BACKEND_TAG=drill DEPLOY_MAINTENANCE_CMD= bash scripts/deploy-backend.sh --image {{tag}} "
                 f"-p sran-test -f docker-compose.test-stack.yml --no-ops-watch --probe-url http://127.0.0.1:18080/live")
        # (AM24b's drill: probed on the backend's own port, 18081, each deploy showed a gap of 7.5-8.5 s — the port
        # is the old container's and goes with it; through the test gateway, as live's probe goes, the bridge carries
        # the requests)
        L += ["## Practise on the test stack first", "",
              "The backend's way back, on the test stack (compose project `sran-test`), under a tag of its own — "
              "`drill` — so nothing live starts from is moved (the deploy refuses a tag another project runs):", "",
              "```bash",
              f"docker tag {be['current_tag']} {be['repo']}:drill",
              f"TS_BACKEND_TAG=drill bash scripts/gate.sh walk -- env AM7_NO_NER=1 bash scripts/agent-market/test_stack.sh "
              f"up {(edge or be)['current_rev']}",
              drill.replace("{tag}", be["rollback_tag"]),
              drill.replace("{tag}", be["current_tag"]),
              "bash scripts/gate.sh stack-stop", "```", "",
              "(`gate.sh walk --` holds the gate's lock and waits for the host to have room before the stack starts; "
              "`stack-stop` stops it and removes nothing.) The drill leaves two tags, `drill` and "
              f"`rollback-{be['current_rev']}` — both the image live runs now; it moves no tag live starts from.", "",
              f"It worked when each deploy ends `done: backend … healthy revision …` — {be['rollback_rev']}, then "
              f"{be['current_rev']} — with `probe: … 0 failed`. The probe asks the test gateway (18080), as live's "
              "asks live's: the backend's own port (18081) goes down with the old container whatever the bridge does.", ""]
    L += ["## Services with nothing to roll back", "",
          "| service | compose file | why |", "|---|---|---|"]
    for key in sorted(k for k, e in S.items() if e["kind"] not in ("backend", "swap", "with")):
        e = S[key]
        why = {
            "pinned": lambda: f"`{e['pinned']}`, pinned in the file: nothing built here, no earlier image kept. "
                              "Another version is a change to the file; going back would not undo data written in "
                              "the newer one's format.",
            "nothing-kept": lambda: f"built here (`{e['current_tag']}`, revision {e['current_rev'] or 'none'}), but "
                                    "no earlier image of it is kept on this host: there is nothing to go back to.",
            "elsewhere": lambda: f"live runs it from `{e['elsewhere']}` (above).",
            "oneshot": lambda: "a one-shot: it ran once (exit 0) when its project started; nothing runs to roll back.",
            "off": lambda: ("not run on live: " + (f"its profile `{e['profiles'][0]}` is" if len(e["profiles"]) == 1
                                                     else "its profiles " + ", ".join(f"`{x}`" for x in e["profiles"])
                                                     + " are") + " not started."
                            if e.get("profiles") else "not run on live."),
        }[e["kind"]]()
        L.append(f"| {e['name']} | {e['files'][0]} | {why} |")
    for e in withs.values():
        L.append(f"| {e['name']} | {e['files'][0]} | moves with {e['with']} (above): one image. |")
    L += ["", STATE_OPEN, state_text(state), STATE_CLOSE, ""]
    return "\n".join(L)


def state_text(state: dict) -> str:
    """The state as JSON, one line per service (a deploy's change to the page is then a line per service moved)."""
    rows = [f" {json.dumps(k)}: {json.dumps(v, sort_keys=True, ensure_ascii=False)}"
            for k, v in sorted(state["services"].items())]
    return '{"written": ' + json.dumps(state["written"]) + ', "services": {\n' + ",\n".join(rows) + "\n}}"


def state_of(text: str) -> dict | None:
    i = text.rfind(STATE_OPEN)
    if i < 0:
        return None
    j = text.find(STATE_CLOSE, i)
    try:
        return json.loads(text[i + len(STATE_OPEN):j])
    except ValueError:
        return None


# ── the commands ────────────────────────────────────────────────────────────────────────────────────────────────

def write() -> int:
    if not docker_here():
        print("rollback_book write: no Docker here — it is written on the host live runs on", file=sys.stderr)
        return 2
    state = build_state()
    BOOK.parent.mkdir(parents=True, exist_ok=True)
    BOOK.write_text(render(state), encoding="utf-8", newline="\n")
    kinds = {}
    for e in state["services"].values():
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    print(f"wrote {BOOK.relative_to(ROOT).as_posix()}: {len(state['services'])} services "
          f"({', '.join(f'{v} {k}' for k, v in sorted(kinds.items()))})")
    return 0


def check() -> int:
    red = 0

    def say(verdict: str, what: str):
        nonlocal red
        red |= verdict == "RED"
        print(f"rollback: {verdict:4} {what}")

    try:
        text = BOOK.read_text(encoding="utf-8")
    except OSError:
        text = ""
    state = state_of(text)
    if state is None:
        say("RED", f"{BOOK.relative_to(ROOT).as_posix()} is missing or holds no state: python scripts/ops/rollback_book.py write")
        for what in ("every service has a line", "not edited by hand", "every tag it names exists",
                     "live runs what it calls current", "no deploy recorded since disagrees"):
            say("RED", what + ": no runbook to check")
        return 1
    S = state["services"]
    # 1. every service in the compose files has a line, and no line names a service they no longer hold
    want = {s["key"] for s in compose_services()}
    missing, gone = sorted(want - set(S)), sorted(set(S) - want)
    say("RED" if missing or gone else "ok",
        f"every service in the compose files has a line ({len(want)} services)"
        + (f" — no line: {', '.join(missing)}" if missing else "")
        + (f" — a line for what the files no longer hold: {', '.join(gone)}" if gone else ""))
    # 2. the text is what write makes of its state
    same = render(state) == text
    say("ok" if same else "RED", "the page is what `write` makes of its recorded state"
        + ("" if same else " — it was edited by hand: python scripts/ops/rollback_book.py write"))
    named = sorted({e[k] for e in S.values() for k in ("current_tag", "rollback_tag") if e.get(k)})
    built = {k: e for k, e in S.items() if e["kind"] in ("backend", "swap", "with", "nothing-kept")}
    if not docker_here():
        say("skip", f"every tag it names exists ({len(named)} tags) — no Docker here")
        say("skip", f"live runs what it calls current ({len(built)} services) — no Docker here")
    else:
        # 3. every tag it names exists
        absent = [t for t in named if sh("docker", "image", "inspect", "--format", "{{.Id}}", t)[0] != 0]
        say("RED" if absent else "ok", f"every tag it names exists ({len(named)} tags)"
            + (f" — not on this host: {', '.join(absent)}" if absent else ""))
        # 4. live runs what it calls current
        live = {}
        for project, _, _ in SETS:
            for c in containers(project):
                if c["status"] == "running":
                    live[(project, c["service"], tuple(c["files"]))] = c
        wrong = []
        for k, e in sorted(built.items()):
            c = live.get((e["project"], e["name"], tuple(sorted(e["files"]))))
            if c is None:
                wrong.append(f"{e['name']} is not running")
            elif (c["rev"] or "") != (e.get("current_rev") or ""):
                wrong.append(f"{e['name']} runs {c['rev'] or 'an image with no revision'}, the page says "
                             f"{e.get('current_rev') or 'none'}")
            elif e.get("current_id") and not c["id"].replace("sha256:", "").startswith(e["current_id"]):
                wrong.append(f"{e['name']} runs image {c['id'].replace('sha256:', '')[:12]}, the page says {e['current_id']}")
        say("RED" if wrong else "ok", f"live runs what it calls current ({len(built)} services)"
            + (" — " + "; ".join(wrong) + ": python scripts/ops/rollback_book.py write, then commit it" if wrong else ""))
    # 5. no deploy recorded since it was written disagrees with it
    live_projects = {project for project, _, _ in SETS}
    rows = [r for r in records() if r["time"] > state["written"] and r["project"] in live_projects]
    if not RECORD.exists():
        say("skip", "no deploy recorded since it was written disagrees with it — no deploy record on this host")
    else:
        late = []
        for r in rows:
            e = next((e for e in S.values() if e["project"] == r["project"] and e["name"] == r["service"]
                      and e["kind"] in ("backend", "swap", "with", "nothing-kept")), None)
            if e and (r["rev"] if r["rev"] != "-" else "") != (e.get("current_rev") or ""):
                late.append(f"{r['service']} deployed at {r['rev']} by {r['tool']} at {r['time']}, the page says "
                            f"{e.get('current_rev') or 'none'}")
        say("RED" if late else "ok", f"no deploy recorded since it was written disagrees with it ({len(rows)} since)"
            + (" — " + "; ".join(late) + ": python scripts/ops/rollback_book.py write, then commit it" if late else ""))
    return 1 if red else 0


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):  # (a cp874 console cannot print the page's dashes)
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    if argv[1:2] == ["write"]:
        return write()
    if argv[1:2] == ["check"]:
        return check()
    print(__doc__.split("\n\n")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
