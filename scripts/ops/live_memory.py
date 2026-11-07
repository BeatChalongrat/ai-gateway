#!/usr/bin/env python3
"""The memory guard — what each live container may take, what the others must leave it, and whose turn
it is when the Docker VM runs out.

Live and everything a gate starts (the test stack, sandboxes, image builds) share one Docker VM on this
host. Before this, nothing said who gives way: a gate could push the VM into its out-of-memory killer
with a database as likely a victim as a test container. Three settings per live service, declared in the
compose files and nowhere else:

    mem_limit         a ceiling — one runaway service cannot take the VM
    mem_reservation   the floor the kernel leaves it when memory is short
    oom_score_adj     its turn under pressure: negative for live (databases last of all), so the kernel
                      takes a gate's containers first (those are started with a positive value)

This script reads the three from the resolved compose configuration and makes the running containers
agree, without restarting any of them:

    python scripts/ops/live_memory.py status     what each live container has, against what compose says
    python scripts/ops/live_memory.py apply      docker update (ceiling, floor) + the turn, set on the processes
    python scripts/ops/live_memory.py check      exit 1 and name every container that does not agree

A container that is recreated takes all three from compose by itself. `apply` is for the ones already
running (a ceiling and a floor can be changed in place; the turn is written to the processes, and is the
container's own from its next recreation on). The gate runs `check` before it starts anything.

Nothing of the configuration is printed but service names and these three values: the resolved
configuration holds the deployment's secrets and stays in this process.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# What "live" is: every compose project started from this checkout that is running now, except the
# test stacks (their names say so). Which compose files each was started with is read from the
# containers' own labels — nothing here names a project or a service.
NOT_LIVE = ("test",)
HELPER_IMAGE = os.environ.get("LIVE_MEMORY_HELPER_IMAGE", "alpine:3")


def run(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", cwd=ROOT, **kw)


def to_bytes(v) -> int:
    if v in (None, "", 0, "0"):
        return 0
    s = str(v).strip().lower()
    units = {"k": 1 << 10, "m": 1 << 20, "g": 1 << 30, "kb": 1 << 10, "mb": 1 << 20, "gb": 1 << 30}
    for suffix in sorted(units, key=len, reverse=True):
        if s.endswith(suffix):
            return int(float(s[: -len(suffix)]) * units[suffix])
    return int(float(s))


def _same_dir(a: str, b: str) -> bool:
    norm = lambda p: os.path.normcase(os.path.normpath(p.replace("\\", "/")))
    return norm(a) == norm(b)


def running() -> list[dict]:
    """Every running container of the live projects, with what it has now and the compose files it was
    started with."""
    ids = run(["docker", "ps", "-q", "--filter", "label=com.docker.compose.project"]).stdout.split()
    rows = []
    for c in json.loads(run(["docker", "inspect", *ids]).stdout or "[]") if ids else []:
        labels = c["Config"].get("Labels") or {}
        project = labels.get("com.docker.compose.project", "")
        if not _same_dir(labels.get("com.docker.compose.project.working_dir", ""), ROOT) or any(w in project for w in NOT_LIVE):
            continue
        rows.append({"name": c["Name"].lstrip("/"), "project": project, "service": labels.get("com.docker.compose.service", ""),
                     "files": labels.get("com.docker.compose.project.config_files", ""),
                     "pid": c["State"].get("Pid") or 0, "limit": c["HostConfig"].get("Memory") or 0,
                     "reservation": c["HostConfig"].get("MemoryReservation") or 0, "oom_config": c["HostConfig"].get("OomScoreAdj") or 0})
    return rows


def declared(have: list[dict]) -> dict[str, dict]:
    """service → what compose declares, for every compose invocation a running live container came from."""
    out: dict[str, dict] = {}
    for project, files in sorted({(c["project"], c["files"]) for c in have}):
        args = [a for f in files.split(",") if f for a in ("-f", f)]
        r = run(["docker", "compose", "-p", project, *args, "--profile", "*", "config", "--format", "json"])
        if r.returncode != 0:
            raise SystemExit(f"compose config failed for {project}: {r.stderr.strip()[:300]}")
        for name, s in (json.loads(r.stdout).get("services") or {}).items():
            res = ((s.get("deploy") or {}).get("resources") or {})
            # (keyed by the invocation too: two compose files of one project may each define a service of the same name)
            out[f"{project}|{files}|{name}"] = {
                "project": project, "service": name,
                "limit": to_bytes(s.get("mem_limit") or (res.get("limits") or {}).get("memory")),
                "reservation": to_bytes(s.get("mem_reservation") or (res.get("reservations") or {}).get("memory")),
                "oom": s.get("oom_score_adj"),
            }
    return out


def runtime_oom(pids: list[int], write: dict[int, int] | None = None) -> dict[int, int]:
    """The turn of each container's first process as the kernel has it — and, with `write`, set on every
    process of those containers (a child takes its parent's, so what starts later has it too)."""
    if not pids:
        return {}
    script = ["set -u"]
    for pid, adj in (write or {}).items():
        # every process whose cgroup line is the first process's own: the same string for the whole
        # container, whichever cgroup namespace the helper reads it from
        script.append(f'want=$(cat /proc/{pid}/cgroup 2>/dev/null); [ -n "$want" ] && for d in /proc/[0-9]*; do '
                      f'[ "$(cat $d/cgroup 2>/dev/null)" = "$want" ] && echo {adj} > $d/oom_score_adj 2>/dev/null; done')
    for pid in pids:
        script.append(f'echo "{pid} $(cat /proc/{pid}/oom_score_adj 2>/dev/null || echo ?)"')
    # (the helper itself is the first to go if memory is short while it runs)
    r = run(["docker", "run", "--rm", "--privileged", "--pid=host", "--cgroupns=host", "--network", "none", "--oom-score-adj", "1000",
             HELPER_IMAGE, "sh", "-c", "\n".join(script)])
    if r.returncode != 0:
        raise SystemExit(f"the helper could not read the processes: {r.stderr.strip()[:300]}")
    out = {}
    for line in r.stdout.splitlines():
        a, _, b = line.partition(" ")
        if a.isdigit() and b.lstrip("-").isdigit():
            out[int(a)] = int(b)
    return out


def mb(n: int) -> str:
    return "—" if not n else (f"{n / (1 << 30):.1f}g" if n >= 1 << 30 else f"{n // (1 << 20)}m")


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):  # (a Windows console's own code page cannot print the table)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd not in ("status", "apply", "check"):
        print(__doc__.split("\n\n")[3], file=sys.stderr)
        return 2
    have = running()
    want = declared(have)
    if cmd == "apply":
        turns = {}
        for c in have:
            w = want.get(f"{c['project']}|{c['files']}|{c['service']}")
            if not w:
                continue
            args = []
            if w["limit"] and w["limit"] != c["limit"]:
                args += ["--memory", str(w["limit"]), "--memory-swap", str(w["limit"])]
            if w["reservation"] and w["reservation"] != c["reservation"]:
                args += ["--memory-reservation", str(w["reservation"])]
            if args:
                r = run(["docker", "update", *args, c["name"]])
                print(f"{c['name']}: ceiling {mb(w['limit'])} floor {mb(w['reservation'])} → {'set' if r.returncode == 0 else 'NOT set: ' + r.stderr.strip()[:160]}")
            if w["oom"] is not None and c["pid"]:
                turns[c["pid"]] = int(w["oom"])
        runtime_oom(list(turns), write=turns)
        have = running()
    now = runtime_oom([c["pid"] for c in have if c["pid"]])
    bad = []
    floor = 0
    print(f"{'container':38} {'ceiling':>8} {'floor':>7} {'turn':>6}   compose says")
    for c in sorted(have, key=lambda c: (now.get(c["pid"], 0), c["name"])):
        w = want.get(f"{c['project']}|{c['files']}|{c['service']}")
        turn = now.get(c["pid"])
        says = "(not a service of the live compose files)" if not w else f"{mb(w['limit'])} / {mb(w['reservation'])} / {w['oom'] if w['oom'] is not None else '—'}"
        ok = bool(w) and (not w["limit"] or w["limit"] == c["limit"]) and (not w["reservation"] or w["reservation"] == c["reservation"]) \
            and (w["oom"] is None or turn == int(w["oom"])) and w["limit"] > 0 and w["oom"] is not None
        if w:
            floor += c["reservation"]
        if not ok:
            bad.append(c["name"])
        print(f"{c['name']:38} {mb(c['limit']):>8} {mb(c['reservation']):>7} {str(turn if turn is not None else '?'):>6}   {says}{'' if ok else '   ← differs'}")
    print(f"\n{len(have)} live containers · floors add up to {mb(floor)} · {len(bad)} not as compose says")
    if cmd == "check" and bad:
        print("run: python scripts/ops/live_memory.py apply", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
