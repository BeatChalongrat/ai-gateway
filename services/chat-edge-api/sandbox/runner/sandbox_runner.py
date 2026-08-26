#!/usr/bin/env python3
"""Sandbox runner — the ONLY process the sandbox container starts.

Phase 13 Part A. Runs inside a locked-down `docker run` (no network,
non-root, cap-drop ALL, no-new-privileges, seccomp default-deny,
read-only rootfs, tmpfs scratch, memory/pids/cpu caps). It reads a job
spec from the read-only input mount, executes it, and writes results
back to the writable output mount. It NEVER trusts anything on the host
side: the only things it can reach are the files under /job/in (ro) and
/job/out (rw) plus tmpfs /tmp.

Mounts (set by the Go caller `internal/sandbox`):

    /job/in    read-only   job.json + any input files
    /job/out   read-write  artifacts + result sidecars
    /tmp       tmpfs       scratch (HOME, MPLCONFIGDIR point here)

Job spec (/job/in/job.json):

    {
      "mode": "script",                 # "script" | "code"
      "script": "excel_executor.py",    # script mode: baked-in script name
      "args":  ["plan.json", "in/source.xlsx", "out/output.xlsx"],
      "code":  "...python source...",   # code mode (Part B): free-form
      "wall_timeout_sec": 90,           # server-enforced, belt-and-braces
      "max_output_bytes": 1048576       # stdout/stderr cap per stream
    }

`args` entries are resolved: a bare name or "in/<x>" is looked up under
/job/in; "out/<x>" is placed under /job/out. This keeps the plan-exec
scripts' `<plan> <source> <output>` argv contract intact while pinning
every path inside the two mounts — model-supplied argv can never point
at a host path (there are none mounted) and `..` cannot escape because
the mounts are the only writable/readable surfaces.

Result sidecars (written to /job/out, read back by Go):

    __stdout        raw child stdout (capped)
    __stderr        raw child stderr (capped)
    __meta.json     {"exit":int,"truncated":bool,"timed_out":bool,"error":str}

Everything else the child leaves in /job/out is an artifact.
"""

import json
import os
import subprocess
import sys

JOB_IN = "/job/in"
JOB_OUT = "/job/out"
SCRIPTS = "/sandbox/scripts"

DEFAULT_WALL = 90
DEFAULT_MAX_OUTPUT = 1 << 20  # 1 MiB per stream


def _resolve_arg(a: str) -> str:
    """Map a job-spec arg to a concrete in-sandbox path.

    "out/<x>"  -> /job/out/<x>   (writable target)
    "in/<x>"   -> /job/in/<x>    (read-only input)
    "<x>"      -> /job/in/<x>    (read-only input, bare name)
    Absolute-looking or traversal args are collapsed to a basename under
    the intended mount so nothing can point outside /job.
    """
    a = a.strip()
    if a.startswith("out/"):
        base = os.path.basename(a[len("out/"):])
        return os.path.join(JOB_OUT, base)
    if a.startswith("in/"):
        base = os.path.basename(a[len("in/"):])
        return os.path.join(JOB_IN, base)
    # bare token — treat as an input file name (plan.json, source.xlsx)
    return os.path.join(JOB_IN, os.path.basename(a))


def _detect_runtime():
    """FIX14 §2 — report the isolation boundary FROM INSIDE this running job.

    Ground truth, not configuration: gVisor (runsc) intercepts syscalls with
    its own user-space kernel and stamps "gVisor" into /proc/version and
    /proc/sys/kernel/*; a plain runc container shows the real host kernel.
    The Go caller compares this against the runtime it REQUESTED and refuses
    the result on a mismatch — so a silent fall-back to the weaker runtime
    cannot pass as hardened. Returns "gvisor" or "standard" (never raises).
    """
    for path in ("/proc/version", "/proc/sys/kernel/osrelease", "/proc/sys/kernel/version"):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                if "gvisor" in fh.read().lower():
                    return "gvisor"
        except OSError:
            continue
    return "standard"


def _write_meta(exit_code, truncated, timed_out, error=""):
    with open(os.path.join(JOB_OUT, "__meta.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "exit": exit_code,
                "truncated": truncated,
                "timed_out": timed_out,
                "error": error,
                # FIX14 §2 — the runtime this job ACTUALLY executed under,
                # measured from inside the run.
                "runtime": _detect_runtime(),
            },
            f,
        )


def _cap(buf: bytes, limit: int):
    if buf is None:
        return b"", False
    if len(buf) > limit:
        return buf[:limit], True
    return buf, False


def main() -> int:
    # tmpfs-backed writable spots for libs that insist on a HOME/config.
    os.environ["HOME"] = "/tmp"
    os.environ["MPLCONFIGDIR"] = "/tmp"
    os.environ["TMPDIR"] = "/tmp"
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

    try:
        with open(os.path.join(JOB_IN, "job.json"), encoding="utf-8") as f:
            job = json.load(f)
    except Exception as exc:  # noqa: BLE001
        _write_meta(97, False, False, f"job spec unreadable: {exc}")
        return 97

    mode = job.get("mode", "script")
    wall = int(job.get("wall_timeout_sec") or DEFAULT_WALL)
    max_out = int(job.get("max_output_bytes") or DEFAULT_MAX_OUTPUT)

    if mode == "script":
        script = os.path.basename(str(job.get("script", "")))
        script_path = os.path.join(SCRIPTS, script)
        if not script or not os.path.isfile(script_path):
            _write_meta(96, False, False, f"unknown script: {script}")
            return 96
        argv = [sys.executable, script_path]
        for a in job.get("args", []) or []:
            argv.append(_resolve_arg(str(a)))
    elif mode == "code":
        # PART B SEAM: free-form model code. Runs under the identical
        # lockdown (this same container/flags/seccomp). Part C's DLP
        # egress gate inspects stdout/artifacts AFTER this returns — the
        # runner stays dumb and just executes. Code is delivered as a
        # file so it never touches the argv.
        code = job.get("code")
        if not isinstance(code, str) or not code:
            _write_meta(96, False, False, "code mode: empty code")
            return 96
        code_path = "/tmp/__user_code.py"
        with open(code_path, "w", encoding="utf-8") as f:
            f.write(code)
        argv = [sys.executable, code_path]
        for a in job.get("args", []) or []:
            argv.append(_resolve_arg(str(a)))
    else:
        _write_meta(96, False, False, f"unknown mode: {mode}")
        return 96

    timed_out = False
    try:
        proc = subprocess.run(
            argv,
            cwd="/tmp",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=wall,
            check=False,
        )
        out, exit_code, err = proc.stdout, proc.returncode, proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        out = exc.stdout or b""
        err = (exc.stderr or b"") + b"\n[sandbox] wall-clock timeout"
        exit_code = 124
    except Exception as exc:  # noqa: BLE001
        _write_meta(95, False, False, f"exec failed: {exc}")
        return 95

    out, t1 = _cap(out, max_out)
    err, t2 = _cap(err, max_out)
    with open(os.path.join(JOB_OUT, "__stdout"), "wb") as f:
        f.write(out)
    with open(os.path.join(JOB_OUT, "__stderr"), "wb") as f:
        f.write(err)
    _write_meta(exit_code, t1 or t2, timed_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
