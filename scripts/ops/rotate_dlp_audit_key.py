#!/usr/bin/env python3
"""Rotate DLP_AUDIT_KEY — every step by NAME; no step can print, log or be handed a key.

DLP_AUDIT_KEY encrypts one column (request_logs.input_raw_encrypted, the prompt as typed; the admin trace
view decrypts it) and signs nothing. A rotation is therefore: a new key in .env, the backend restarted
with it, and the rows written under the old key rewritten under the new one
(backend/scripts/reencrypt_dlp_audit_key.py). In order:

    bash scripts/gate.sh backup
    python scripts/ops/rotate_dlp_audit_key.py new        # .env: the key in use becomes DLP_AUDIT_KEY_OLD,
                                                          # a fresh 32-byte key becomes DLP_AUDIT_KEY
    scripts/deploy-backend.sh --image <the image live runs> --no-ops-watch
    python scripts/ops/rotate_dlp_audit_key.py check      # how many rows open under which key (writes nothing)
    python scripts/ops/rotate_dlp_audit_key.py apply      # rewrites the old key's rows under the new key
    python scripts/ops/rotate_dlp_audit_key.py check      # must say: 0 to rewrite, 0 unreadable

DLP_AUDIT_KEY_OLD stays in .env afterwards: backups taken before the rotation hold rows only it opens.
`drop-old` removes that line — the owner's decision, once those backups have aged out or are not wanted.

The keys travel from .env to the re-encryption tool through the environment of one `docker exec`
(`-e NAME`, never `-e NAME=value`), so they are on no command line and in no process listing.
"""
from __future__ import annotations

import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = Path(os.environ.get("ROTATE_ENV_FILE") or ROOT / ".env")
TOOL = ROOT / "backend" / "scripts" / "reencrypt_dlp_audit_key.py"
NAME, OLD = "DLP_AUDIT_KEY", "DLP_AUDIT_KEY_OLD"
LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def read() -> tuple[list[str], str]:
    raw = ENV_FILE.read_bytes().decode("utf-8")
    eol = "\r\n" if "\r\n" in raw else "\n"
    return raw.split(eol), eol


def write(lines: list[str], eol: str) -> None:
    # in place, same file: a container that has .env mounted keeps seeing it
    with open(ENV_FILE, "r+b") as f:
        f.seek(0)
        f.write(eol.join(lines).encode("utf-8"))
        f.truncate()


def value(lines: list[str], name: str) -> str | None:
    for line in lines:
        m = LINE.match(line)
        if m and m.group(1) == name:
            return m.group(2).strip().strip('"')
    return None


def new() -> int:
    lines, eol = read()
    if value(lines, OLD) is not None:
        print(f"{OLD} is already in the file: a rotation was started and not finished (check, apply, then drop-old).")
        return 2
    if not value(lines, NAME):
        print(f"{NAME} is not set in the file: there is nothing to rotate.")
        return 2
    fresh = secrets.token_hex(32)
    out = []
    for line in lines:
        m = LINE.match(line)
        if m and m.group(1) == NAME:
            out += [f"{NAME}={fresh}", f"{OLD}={m.group(2)}"]
        else:
            out.append(line)
    write(out, eol)
    print(f"{NAME}: a new 32-byte key is in the file. The key that was in use is kept as {OLD}.")
    return 0


def drop_old() -> int:
    lines, eol = read()
    if value(lines, OLD) is None:
        print(f"{OLD} is not in the file.")
        return 0
    write([line for line in lines if not (LINE.match(line) and LINE.match(line).group(1) == OLD)], eol)
    print(f"{OLD} removed. Rows in backups taken before the rotation can no longer be opened.")
    return 0


def reencrypt(apply: bool) -> int:
    lines, _ = read()
    old, current = value(lines, OLD), value(lines, NAME)
    if not old or not current:
        print(f"both {NAME} and {OLD} must be in the file (run `new` first).")
        return 2
    container = os.environ.get("ROTATE_BACKEND_CONTAINER", "ai-gateway-backend-1")
    env = {**os.environ, "DLP_AUDIT_KEY_OLD": old, "DLP_AUDIT_KEY_NEW": current, "MSYS_NO_PATHCONV": "1"}
    cmd = ["docker", "exec", "-i", "-e", "DLP_AUDIT_KEY_OLD", "-e", "DLP_AUDIT_KEY_NEW", container, "python", "-"]
    with open(TOOL, "rb") as tool:
        return subprocess.run(cmd + (["--apply"] if apply else []), stdin=tool, env=env).returncode


def main(argv: list[str]) -> int:
    verb = argv[1] if len(argv) == 2 else ""
    if verb == "new":
        return new()
    if verb in ("check", "apply"):
        return reencrypt(verb == "apply")
    if verb == "drop-old":
        return drop_old()
    print("new | check | apply | drop-old   (see the top of this file for the order)")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
