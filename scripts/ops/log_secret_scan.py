#!/usr/bin/env python3
"""FIX129 B — fail if a secret from .env shows up in a container log.

    python scripts/ops/log_secret_scan.py --env /path/to/.env [--project ai-gateway] [--since 24h]

What counts as a secret (values are NEVER printed, only the key names):
  * the value of every .env key whose NAME matches (PASSWORD|SECRET|KEY|TOKEN|DSN);
  * for ANY .env value that is a URL with ``user:pass@`` or a libpq DSN with
    ``password=...``, the password part;
  * (FIX130) the path of ``ALERT_NTFY_URL`` / ``*WEBHOOK_URL`` — the ntfy
    topic is that channel's only secret;
  * values shorter than 8 characters and the well-known dev defaults
    (``postgres``, ``changeme``, anything containing ``change-in-prod``) are
    ignored — they would match prose, not leaks.

It runs ``docker logs`` (stdout+stderr, optionally ``--since``) for every
container whose ``com.docker.compose.project`` label equals ``--project``
(running or not) and reports, per container, which KEY NAMES were found and
how many times. Exit 1 when anything is found, 0 when clean, 2 on a usage
error. Read-only: it never writes to a container.

Evidence for the B3 finding: on 2026-09-23 this was RED on
ai-gateway-rate-limiter-1 (the "rate-limiter booting" line printed
REDIS_URL with the password) and green everywhere else. See
docs/ops/monitoring.md, "log secret scan".
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote, urlsplit

SECRET_KEY_RE = re.compile(r"(PASSWORD|SECRET|KEY|TOKEN|DSN)", re.I)
URL_CRED_RE = re.compile(r"^[a-z][a-z0-9+.-]*://[^/@\s]*:[^/@\s]+@", re.I)
LIBPQ_PASSWORD_RE = re.compile(r"(?:^|\s)password=(\S+)")
CHANNEL_URL_KEY_RE = re.compile(r"^(?:TS_)?(ALERT_NTFY_URL|[A-Z0-9_]*WEBHOOK_URL)$")
DEV_DEFAULTS = {"postgres", "changeme"}
MIN_LEN = 8


def parse_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def is_ignorable(value: str) -> bool:
    return (len(value) < MIN_LEN or value in DEV_DEFAULTS
            or "change-in-prod" in value or "change_in_prod" in value)


def collect_secrets(env: dict[str, str]) -> dict[str, set[str]]:
    """secret value -> the key names it came from (a value may serve several keys)."""
    secrets: dict[str, set[str]] = defaultdict(set)
    for key, value in env.items():
        if SECRET_KEY_RE.search(key) and not is_ignorable(value):
            secrets[value].add(key)
        # a URL / DSN with an embedded password, under ANY key name
        if URL_CRED_RE.match(value):
            try:
                pw = urlsplit(value).password
            except ValueError:
                pw = None
            for candidate in {pw, unquote(pw) if pw else None}:
                if candidate and not is_ignorable(candidate):
                    secrets[candidate].add(f"{key} (url password)")
        for pw in LIBPQ_PASSWORD_RE.findall(value):
            if not is_ignorable(pw):
                secrets[pw].add(f"{key} (dsn password)")
        # FIX130 E4 — an alert channel URL is a credential: an ntfy topic is
        # the channel's only secret (anyone who knows it reads the alerts)
        # and a webhook's path often is. The path is looked for on its own.
        if CHANNEL_URL_KEY_RE.match(key) and value:
            try:
                path = urlsplit(value).path.strip("/")
            except ValueError:
                path = ""
            if path and not is_ignorable(path):
                secrets[path].add(f"{key} (topic/path)")
    return secrets


def docker(*args: str) -> bytes:
    proc = subprocess.run(["docker", *args], capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args[:2])} failed: {proc.stderr.decode(errors='replace').strip()[:200]}")
    return proc.stdout


def containers_of(project: str) -> list[str]:
    out = docker("ps", "-a", "--filter", f"label=com.docker.compose.project={project}", "--format", "{{.Names}}")
    return sorted(n for n in out.decode(errors="replace").split() if n)


def container_logs(name: str, since: str | None) -> bytes:
    args = ["logs"]
    if since:
        args += ["--since", since]
    args.append(name)
    proc = subprocess.run(["docker", *args], capture_output=True)  # stderr is the container's stderr stream
    return proc.stdout + proc.stderr


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env", required=True, help="path to the .env whose values are the secrets to look for")
    ap.add_argument("--project", default="ai-gateway", help="compose project label to scan (default ai-gateway)")
    ap.add_argument("--since", default=None, help="docker logs --since value, e.g. 24h or an RFC3339 time")
    args = ap.parse_args()

    env_path = Path(args.env)
    if not env_path.is_file():
        print(f"usage: --env {env_path} is not a file", file=sys.stderr)
        return 2
    secrets = collect_secrets(parse_env(env_path))
    if not secrets:
        print(f"no secret values found in {env_path.name} (nothing to scan for)")
        return 0
    names = containers_of(args.project)
    if not names:
        print(f"no containers with label com.docker.compose.project={args.project}", file=sys.stderr)
        return 2

    print(f"log secret scan: project={args.project} containers={len(names)} "
          f"secret values={len(secrets)} keys={sum(len(k) for k in secrets.values())} since={args.since or 'all'}")
    total_hits = 0
    for name in names:
        data = container_logs(name, args.since)
        hits: dict[str, int] = defaultdict(int)
        first_line: dict[str, int] = {}
        for value, keys in secrets.items():
            needle = value.encode()
            count = data.count(needle)
            if count:
                label = ", ".join(sorted(keys))
                hits[label] += count
                # line number of the first hit, for the operator; never the line itself
                first_line.setdefault(label, data[: data.find(needle)].count(b"\n") + 1)
        if hits:
            total_hits += sum(hits.values())
            print(f"  RED   {name}: " + "; ".join(f"{k} x{v} (first at log line {first_line[k]})" for k, v in sorted(hits.items())))
        else:
            print(f"  ok    {name}: 0 hits in {len(data)} bytes")
    if total_hits:
        print(f"RESULT: RED - {total_hits} secret occurrence(s) in container logs")
        return 1
    print("RESULT: green - no secret value from the env file appears in any container log")
    return 0


if __name__ == "__main__":
    sys.exit(main())
